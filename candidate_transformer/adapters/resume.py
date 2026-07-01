"""Resume source adapter (unstructured).

Given a resume as a **PDF or DOCX** (Req 1.7), this adapter first extracts the
document's plain text and then applies lightweight *section/regex extractors* to
pull candidate fields out of the free-form prose: contact info (emails, phones,
profile links, location), the skills section, the experience section, and the
education section. The design's "Unstructured Source Adapters" entry names this
flow ("extract text then applies section/regex extractors for contact info,
experience, education, and a skills section") and the ``pdf_section_skills``
extraction method (Req 2.5).

Faithful extraction, not normalization
--------------------------------------
Like every adapter, this one performs *faithful* extraction only -- it records
exactly what the document contained (minus surrounding whitespace) together with
the extraction ``method`` for each value, and **never invents a value** (Req 2.4,
2.6). When a section or field is absent the corresponding value is recorded as a
null :class:`FieldValue`. Canonical formatting (E.164 phones, ``YYYY-MM`` dates,
ISO country codes, ``Canonical_Skill_Name`` skills) is the job of the downstream
Normalize stage, not this adapter.

Text extraction is done in :meth:`~ResumeAdapter.ingest` so the heavy
PDF/DOCX libraries are touched once per source and :meth:`~ResumeAdapter.extract`
operates on plain text. ``.txt`` content is also accepted (read as-is) so a
plain-text resume fixture can be driven through the same section parsers by
tagging the reference with ``source_type="resume"``.

Robustness (Req 10.3, 18.1): an unreadable/corrupt document raises
:class:`IngestError` at the ingest boundary (the only exception ``ingest`` may
raise); ``extract`` is defensive and degrades to whatever it can find rather than
crashing.
"""

from __future__ import annotations

import io
import re

from candidate_transformer.models import (
    EducationEntry,
    ExperienceEntry,
    FieldValue,
    Links,
    Location,
    PerSourceRecord,
)
from candidate_transformer.normalizers.skills import Controlled_Skill_Vocabulary

from ._text_extract import (
    find_emails,
    find_github,
    find_linkedin,
    find_phones,
    find_skill_mentions,
)
from .base import (
    IngestError,
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)

__all__ = ["ResumeAdapter"]

SOURCE_TYPE = "resume"

# ingest appends PDF hyperlink URIs after this marker so extract can separate the
# visible text (used for section/name/skills parsing) from the link URIs (used only
# for contact/profile-link extraction). Keeps URLs out of the skills parser.
_PDF_LINKS_MARKER = "<<<__PDF_LINK_URIS__>>>"

# Extraction-method tags recorded for provenance (Req 2.5).
_METHOD_NAME = "resume_text_name"
_METHOD_EMAIL = "regex_email"
_METHOD_PHONE = "regex_phone"
_METHOD_LINKS = "regex_profile_links"
_METHOD_LOCATION = "resume_contact_line"
_METHOD_SKILLS = "pdf_section_skills"
_METHOD_EXPERIENCE = "resume_section_experience"
_METHOD_EDUCATION = "resume_section_education"
_METHOD_HEADLINE = "resume_section_experience"

# Recognized resume section headers (compared case-insensitively, exact line).
_SECTION_HEADERS = {
    "summary": "summary",
    "professional summary": "summary",
    "profile": "summary",
    "objective": "summary",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "education": "education",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
}

# Section names that are NOT education; the education parser stops when it reaches
# one of these so a following "Projects"/"Experience" block never leaks in (the
# common cause of education over-capture on PDF resumes).
_NON_EDUCATION_SECTIONS = frozenset(
    {
        "projects",
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "skills",
        "technical skills",
        "core skills",
        "certifications",
        "certification",
        "achievements",
        "awards",
        "publications",
        "activities",
        "interests",
        "languages",
        "hobbies",
        "summary",
        "professional summary",
        "profile",
        "objective",
        "contact",
    }
)

# An education entry must carry a real signal: an institution keyword or a degree
# keyword. Lines without either (project bullets, GPA/score lines, prose) are not
# treated as education entries -- honestly omitting noise over inventing entries.
_EDU_INSTITUTION_KEYWORDS = (
    "university",
    "institute",
    "college",
    "school",
    "academy",
    "polytechnic",
)
_EDU_DEGREE_KEYWORDS = (
    "b.e",
    "b.tech",
    "b.s",
    "b.sc",
    "b.a",
    "bachelor",
    "m.e",
    "m.tech",
    "m.s",
    "m.sc",
    "m.a",
    "master",
    "ph.d",
    "phd",
    "mba",
    "diploma",
    "associate",
)

# A month name (full or 3-letter), used to recognize an experience date line.
_MONTH_RE = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
)
# One endpoint of a date range: "March 2019", "2019", or "Present".
_DATE_ENDPOINT_RE = re.compile(
    rf"^(?:{_MONTH_RE}\s+\d{{4}}|\d{{4}}|present|current)$",
    re.IGNORECASE,
)
# A 4-digit year anywhere in a line.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Split a "start - end" range on hyphen/en-dash/em-dash with surrounding spaces.
_RANGE_SPLIT_RE = re.compile(r"\s+[-\u2013\u2014]\s+")


class ResumeAdapter:
    """Adapter for the Resume source (PDF/DOCX) -- unstructured (Req 1.7).

    Satisfies the :class:`~.base.SourceAdapter` protocol. A single instance is
    stateless and reusable across references.
    """

    source_type: str = SOURCE_TYPE
    reliability: float = reliability_of(SOURCE_TYPE)
    priority: int = priority_of(SOURCE_TYPE)

    # -- recognition -------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True for an explicit ``resume`` hint or a ``.pdf``/``.docx`` location.

        A plain ``.txt`` resume is only recognized through the explicit
        ``source_type="resume"`` hint, so it does not collide with the Recruiter
        notes adapter (which claims ``.txt`` by extension).
        """
        if ref.source_type == SOURCE_TYPE:
            return True
        if ref.source_type is not None:
            return False
        loc = ref.location.lower()
        return loc.endswith(".pdf") or loc.endswith(".docx")

    # -- ingest ------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Load the document and extract its plain text.

        Dispatches on the file extension: ``.pdf`` via :mod:`pdfplumber`,
        ``.docx`` via :mod:`python-docx`, anything else read as UTF-8 text (so a
        ``.txt`` resume works when tagged ``source_type="resume"``). Any failure is
        surfaced as :class:`IngestError` so the runner records an ``ingest``-stage
        error and continues (Req 10.1). ``source_id`` is ``ref.location`` (Req 1.9).
        """
        loc = ref.location
        lower = loc.lower()
        try:
            if lower.endswith(".pdf"):
                text = self._extract_pdf_text(loc)
            elif lower.endswith(".docx"):
                text = self._extract_docx_text(loc)
            else:
                with open(loc, "r", encoding="utf-8") as fh:
                    text = fh.read()
        except IngestError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize all load errors
            raise IngestError(
                f"failed to read resume {loc!r}: {exc}"
            ) from exc

        return RawSource(
            source_id=loc,
            source_type=SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    @staticmethod
    def _extract_pdf_text(location: str) -> str:
        """Extract text from a PDF, preferring PyMuPDF, falling back to pdfplumber.

        PyMuPDF (``fitz``) preserves line and whitespace structure markedly better
        than pdfplumber on tightly-typeset/multi-column resumes, which keeps section
        headers (``Education``, ``Projects``, ...) on their own lines so section
        detection works at the source. If PyMuPDF is unavailable or fails, we fall
        back to pdfplumber so extraction still works.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return ResumeAdapter._extract_pdf_text_pdfplumber(location)

        try:
            with fitz.open(location) as document:
                page_texts: list[str] = []
                uris: list[str] = []
                for page in document:
                    page_texts.append(page.get_text("text") or "")
                    # Hyperlink annotations (e.g. a "GitHub"/"LinkedIn" label or icon
                    # linking to the actual URL) carry URLs that are NOT part of the
                    # visible text. Collect them separately -- the link genuinely
                    # exists in the document, so this is faithful extraction, not
                    # invention.
                    for link in page.get_links():
                        uri = link.get("uri")
                        if uri:
                            uris.append(uri)
            visible = "\n".join(page_texts)
            if uris:
                # Keep URIs after a marker so section/skills parsing (which runs on
                # the visible text) never mistakes a URL for a skill.
                return visible + "\n" + _PDF_LINKS_MARKER + "\n" + "\n".join(uris)
            return visible
        except Exception:  # noqa: BLE001 - fall back rather than fail the parse
            return ResumeAdapter._extract_pdf_text_pdfplumber(location)

    @staticmethod
    def _extract_pdf_text_pdfplumber(location: str) -> str:
        """Fallback PDF text extraction via :mod:`pdfplumber`."""
        import pdfplumber

        try:
            with pdfplumber.open(location) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
        except Exception as exc:  # noqa: BLE001
            raise IngestError(
                f"failed to extract PDF text from {location!r}: {exc}"
            ) from exc
        return "\n".join(pages)

    @staticmethod
    def _extract_docx_text(location: str) -> str:
        """Extract text from a DOCX using :mod:`python-docx`."""
        import docx

        try:
            document = docx.Document(location)
        except Exception as exc:  # noqa: BLE001
            raise IngestError(
                f"failed to open DOCX {location!r}: {exc}"
            ) from exc
        return "\n".join(p.text for p in document.paragraphs)

    # -- extract -----------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse the resume text into a single :class:`PerSourceRecord` (Req 2.1).

        Always returns exactly one record (an unstructured source describes one
        candidate). Fields the resume does not contain are recorded as null
        :class:`FieldValue` s -- never invented (Req 2.4, 2.6).
        """
        text = raw.content
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if not isinstance(text, str):
            text = ""

        # Separate the visible text (section/name/skills parsing) from any PDF
        # hyperlink URIs ingest appended after the marker. ``link_text`` includes the
        # URIs so the email/profile-link finders can see them; ``visible_text`` does
        # not, so a URL is never parsed as a skill.
        visible_text, marker, _uris = text.partition(_PDF_LINKS_MARKER)
        link_text = text.replace(_PDF_LINKS_MARKER, "\n") if marker else text

        lines = visible_text.splitlines()
        sections = self._split_sections(lines)

        values: dict[str, FieldValue] = {}

        # -- contact info (whole-document regex, incl. hyperlink URIs) -----
        emails = find_emails(link_text)
        values["emails"] = FieldValue(
            value=emails[0] if emails else None, method=_METHOD_EMAIL
        )
        phones = find_phones(visible_text)
        values["phones"] = FieldValue(
            value=phones[0] if phones else None, method=_METHOD_PHONE
        )

        linkedin = find_linkedin(link_text)
        github = find_github(link_text)
        if linkedin is not None or github is not None:
            values["links"] = FieldValue(
                value=Links(linkedin=linkedin, github=github),
                method=_METHOD_LINKS,
            )
        else:
            values["links"] = FieldValue(value=None, method=_METHOD_LINKS)

        # -- name (first non-empty line of the header block) ---------------
        values["full_name"] = FieldValue(
            value=self._extract_name(lines), method=_METHOD_NAME
        )

        # -- location (from the header/contact lines) ----------------------
        values["location"] = FieldValue(
            value=self._extract_location(sections.get("_header", [])),
            method=_METHOD_LOCATION,
        )

        # -- skills section ------------------------------------------------
        values["skills"] = FieldValue(
            value=self._extract_skills(visible_text, sections.get("skills", [])),
            method=_METHOD_SKILLS,
        )

        # -- experience section -------------------------------------------
        experience = self._extract_experience(sections.get("experience", []))
        values["experience"] = FieldValue(
            value=experience or None, method=_METHOD_EXPERIENCE
        )

        # Headline: the title of the most recent (first) experience entry.
        headline = experience[0].title if experience else None
        values["headline"] = FieldValue(value=headline, method=_METHOD_HEADLINE)

        # -- education section --------------------------------------------
        education = self._extract_education(sections.get("education", []))
        values["education"] = FieldValue(
            value=education or None, method=_METHOD_EDUCATION
        )

        return [
            PerSourceRecord(
                source_id=raw.source_id,
                source_type=raw.source_type,
                values=values,
            )
        ]

    # -- section splitting -------------------------------------------------

    @staticmethod
    def _split_sections(lines: list[str]) -> dict[str, list[str]]:
        """Group ``lines`` into sections keyed by canonical section name.

        Lines before the first recognized header land in the ``"_header"`` bucket
        (the name + contact block). A header line itself is not included in its
        section's body.
        """
        sections: dict[str, list[str]] = {"_header": []}
        current = "_header"
        for line in lines:
            key = line.strip().lower()
            if key in _SECTION_HEADERS:
                current = _SECTION_HEADERS[key]
                sections.setdefault(current, [])
                continue
            sections.setdefault(current, []).append(line)
        return sections

    # -- field extractors --------------------------------------------------

    @staticmethod
    def _extract_name(lines: list[str]) -> str | None:
        """Return the first non-empty line as the candidate name, or ``None``.

        Recorded verbatim (whitespace-trimmed). A line that is itself a section
        header is skipped so a resume that opens with a header still works.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower() in _SECTION_HEADERS:
                continue
            return stripped
        return None

    @staticmethod
    def _extract_location(header_lines: list[str]) -> Location | None:
        """Parse a "City, Region[, Country]" fragment from the contact block.

        Scans the header/contact lines and accepts only a fragment that genuinely
        looks like a place: it must be comma-separated, free of contact noise
        (``@``, URLs), short overall, and have short comma-parts. This deliberately
        rejects prose (e.g. a "Professional Summary" sentence that happens to
        contain commas) so a sentence is never mis-read as a location -- when no
        clean location is present the field stays null (honest over wrong).
        """
        for line in header_lines:
            # Split on the separators commonly used between contact items.
            for part in re.split(r"[|•\u2013\u2014]", line):
                candidate = part.strip()
                if not candidate or "," not in candidate:
                    continue
                low = candidate.lower()
                if "@" in candidate or "http" in low or ".com" in low:
                    continue
                if not any(ch.isalpha() for ch in candidate):
                    continue
                pieces = [p.strip() for p in candidate.split(",") if p.strip()]
                if not pieces:
                    continue
                # Reject sentence-like fragments: a real location has short parts
                # (a city/region/country is at most a few words) and is not long.
                if len(candidate) > 40:
                    continue
                if any(len(piece.split()) > 3 for piece in pieces):
                    continue
                if len(pieces) >= 3:
                    return Location(
                        city=pieces[0], region=pieces[1], country=pieces[2]
                    )
                if len(pieces) == 2:
                    return Location(city=pieces[0], country=pieces[1])
                return Location(city=pieces[0])
        return None

    @staticmethod
    def _clean_skill_token(token: str) -> str | None:
        """Clean one raw skills-section token into a candidate skill, or ``None``.

        Resumes (especially PDF text with collapsed whitespace) glue category
        labels onto the first skill, producing tokens like ``"Backend: Java"`` or
        ``"Tools/Platforms: Postman"``. This strips a leading ``Label:`` prefix by
        taking the text after the last colon, then drops tokens that carry no
        letters (page numbers, bullets, separators) so they never become spurious
        skills. Returns ``None`` when nothing usable remains.
        """
        candidate = token.strip()
        if ":" in candidate:
            # Keep only the part after the final label colon ("Backend: Java" -> "Java").
            candidate = candidate.rsplit(":", 1)[1].strip()
        if not candidate or not any(ch.isalpha() for ch in candidate):
            return None
        return candidate

    @classmethod
    def _extract_skills(cls, text: str, skills_lines: list[str]) -> list[str] | None:
        """Return raw skill tokens from the SKILLS section (or ``None``).

        Prefers the explicit skills section: its lines are joined, split on commas
        (and newlines), each token is cleaned of any leading ``Category:`` label and
        junk via :meth:`_clean_skill_token`, and the raw cleaned strings are kept
        verbatim (canonicalization happens in the Normalize stage). Falls back to a
        vocabulary scan of the whole document when no dedicated section is present.
        Never invents a skill (Req 2.6).
        """
        section_text = "\n".join(skills_lines).strip()
        if section_text:
            raw_tokens = section_text.replace("\n", ",").split(",")
            skills = [
                cleaned
                for tok in raw_tokens
                if (cleaned := cls._clean_skill_token(tok)) is not None
            ]
            return skills or None

        # Fallback: scan the document for known vocabulary mentions.
        mentions = find_skill_mentions(text, Controlled_Skill_Vocabulary)
        return mentions or None

    @classmethod
    def _extract_experience(cls, exp_lines: list[str]) -> list[ExperienceEntry]:
        """Parse the EXPERIENCE section into ordered :class:`ExperienceEntry` items.

        Recognizes the common "Company - Title" header line followed by a
        "Start - End" date line; bullet lines (starting with ``-``/``*``) are
        ignored for field extraction. Dates are recorded verbatim (e.g.
        ``"March 2019"``, ``"Present"``); the Normalize stage converts them to
        ``YYYY-MM``.
        """
        entries: list[ExperienceEntry] = []
        for line in exp_lines:
            stripped = line.strip()
            if not stripped:
                continue

            # A "start - end" date line refines the current entry's dates.
            if cls._is_date_range(stripped):
                start, end = cls._parse_date_range(stripped)
                if entries:
                    entries[-1].start = start
                    entries[-1].end = end
                continue

            # A new entry begins only on a "Company - Title" header line, i.e. a
            # line carrying the range separator. Any other prose (responsibility
            # bullets, whose leading marker some formats strip) is attached as the
            # current entry's summary rather than mistaken for a new role.
            if cls._has_separator(stripped):
                company, title = cls._parse_company_title(stripped)
                if company is None and title is None:
                    continue
                entries.append(ExperienceEntry(company=company, title=title))
            elif entries:
                cls._append_summary(entries[-1], cls._strip_bullet(stripped))
        return entries

    @staticmethod
    def _has_separator(line: str) -> bool:
        """True when ``line`` contains the " - " (or en/em-dash) range separator."""
        return len(_RANGE_SPLIT_RE.split(line, maxsplit=1)) == 2

    @staticmethod
    def _strip_bullet(line: str) -> str:
        """Remove a leading bullet marker (``-``/``*``/``\u2022``) and spaces."""
        return line.lstrip("-*\u2022 \t").strip()

    @staticmethod
    def _append_summary(entry: ExperienceEntry, text: str) -> None:
        """Append ``text`` to ``entry.summary`` (space-joined), ignoring blanks."""
        if not text:
            return
        entry.summary = f"{entry.summary} {text}".strip() if entry.summary else text

    @staticmethod
    def _is_date_range(line: str) -> bool:
        """True when ``line`` is a "start - end" date range."""
        parts = _RANGE_SPLIT_RE.split(line)
        if len(parts) != 2:
            return False
        return bool(
            _DATE_ENDPOINT_RE.match(parts[0].strip())
            and _DATE_ENDPOINT_RE.match(parts[1].strip())
        )

    @staticmethod
    def _parse_date_range(line: str) -> tuple[str | None, str | None]:
        """Split a date-range line into ``(start, end)`` verbatim strings."""
        parts = _RANGE_SPLIT_RE.split(line)
        if len(parts) != 2:
            return (line.strip() or None, None)
        start = parts[0].strip() or None
        end = parts[1].strip() or None
        return (start, end)

    @staticmethod
    def _parse_company_title(line: str) -> tuple[str | None, str | None]:
        """Split a "Company - Title" line into its two parts.

        When no separator is present the whole line is treated as the company.
        """
        parts = _RANGE_SPLIT_RE.split(line, maxsplit=1)
        if len(parts) == 2:
            company = parts[0].strip() or None
            title = parts[1].strip() or None
            return (company, title)
        return (line.strip() or None, None)

    @classmethod
    def _extract_education(cls, edu_lines: list[str]) -> list[EducationEntry]:
        """Parse the EDUCATION section into ordered :class:`EducationEntry` items.

        Robust against PDF section bleed and noise:

        * **stops at the next section** -- scanning halts at the first line that is a
          non-education section header (e.g. ``Projects``/``Experience``), so a
          following block never leaks in (Req 2.6, honesty);
        * **drops noise** -- bullet lines and metric lines (``GPA:`` / ``Score:`` /
          percentages) are skipped;
        * **requires a real signal** -- only a line carrying an institution keyword
          (University/Institute/College/...) anchors an entry; an adjacent
          degree/field detail line (``B.S. in Computer Science, 2015``) enriches it.

        A messy resume therefore yields a few clean institution entries (with null
        degree/year where they cannot be reliably parsed) instead of dozens of
        spurious ones.
        """
        # 1. Trim section bleed + drop obvious noise, keeping order.
        kept: list[str] = []
        for raw in edu_lines:
            line = raw.strip()
            if not line:
                continue
            if cls._is_section_break(line):
                break
            if cls._is_bullet(line) or cls._is_education_noise(line):
                continue
            kept.append(line)

        # 2. Anchor an entry on each institution line; enrich from an adjacent
        #    degree/field detail line when present.
        entries: list[EducationEntry] = []
        i = 0
        while i < len(kept):
            line = kept[i]
            if not cls._has_institution_keyword(line):
                i += 1
                continue

            degree: str | None = None
            field_of_study: str | None = None
            end_year: int | None = cls._parse_year(line)

            # Look ahead one line for a degree/field detail (not another
            # institution); a bare year-only line is not treated as a detail to
            # avoid mis-pairing details that belong to a different school.
            if i + 1 < len(kept):
                nxt = kept[i + 1]
                if not cls._has_institution_keyword(nxt) and (
                    cls._has_degree_keyword(nxt) or " in " in nxt.lower()
                ):
                    degree, field_of_study = cls._parse_degree_field(nxt)
                    end_year = end_year or cls._parse_year(nxt)
                    i += 1

            entries.append(
                EducationEntry(
                    institution=line or None,
                    degree=degree,
                    field=field_of_study,
                    end_year=end_year,
                )
            )
            i += 1
        return entries

    @staticmethod
    def _is_section_break(line: str) -> bool:
        """True when ``line`` is a non-education section header (stop education here)."""
        return line.strip().lower() in _NON_EDUCATION_SECTIONS

    @staticmethod
    def _is_bullet(line: str) -> bool:
        """True for a bullet/responsibility line (``-``/``*``/``\u2022`` prefix)."""
        return line.lstrip()[:1] in {"-", "*", "\u2022", "\u25cf", "\u25aa"}

    @staticmethod
    def _is_education_noise(line: str) -> bool:
        """True for metric/noise lines that are never an education entry.

        Filters GPA/CGPA/score/percentage lines and very short fragments that carry
        no institution or degree signal.
        """
        low = line.lower()
        if low.startswith(("gpa", "cgpa", "score", "percentage", "grade")):
            return True
        return False

    @classmethod
    def _has_institution_keyword(cls, line: str) -> bool:
        """True when ``line`` names an institution (University/Institute/College/...)."""
        low = line.lower()
        return any(kw in low for kw in _EDU_INSTITUTION_KEYWORDS)

    @classmethod
    def _has_degree_keyword(cls, line: str) -> bool:
        """True when ``line`` names a degree (B.S./M.Tech/Bachelor/...)."""
        low = line.lower()
        return any(kw in low for kw in _EDU_DEGREE_KEYWORDS)

    @staticmethod
    def _parse_year(line: str) -> int | None:
        """Return the last 4-digit year found in ``line``, or ``None``."""
        years = _YEAR_RE.findall(line)
        if not years:
            return None
        # findall returns the capture group; re-scan for the full match.
        matches = [m.group(0) for m in _YEAR_RE.finditer(line)]
        return int(matches[-1]) if matches else None

    @staticmethod
    def _parse_degree_field(line: str) -> tuple[str | None, str | None]:
        """Split a degree line into ``(degree, field)`` best-effort.

        ``"B.S. in Computer Science, 2015"`` -> ``("B.S.", "Computer Science")``.
        The trailing year (and its comma) is stripped from the field.
        """
        # Remove a trailing ", 2015"-style year for cleaner field text.
        without_year = _YEAR_RE.sub("", line).strip().rstrip(",").strip()
        if " in " in without_year:
            degree, field_part = without_year.split(" in ", 1)
            degree = degree.strip() or None
            field_part = field_part.strip().rstrip(",").strip() or None
            return (degree, field_part)
        return (without_year or None, None)

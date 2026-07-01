"""LinkedIn profile source adapter (unstructured).

Given a **public LinkedIn profile** (Req 1.6), this adapter extracts the rich set
of signals a profile carries: the display ``name`` (-> ``full_name``), the
``headline``, the free-text ``location``, the ``experience`` and ``education``
histories, the listed ``skills``, and the profile link itself
(-> ``links.linkedin``). The design's "Unstructured Source Adapters" entry names
exactly this flow ("extract name, headline, location, experience, education,
skills, and the profile link").

Deterministic, offline operation
--------------------------------
Live scraping is non-deterministic and unavailable in this environment, so the
adapter operates on a **fetched/sample profile payload** -- a JSON document (the
shape a LinkedIn profile export takes) referenced by the
:class:`~.base.SourceRef`. The payload is read from a local file so identical
inputs always produce identical output (Req 12). The recognized URL hosts let the
registry route a ``linkedin.com`` reference to this adapter; for offline runs the
caller points ``ref.location`` at the local JSON payload and tags it with
``source_type="linkedin"``.

Faithful extraction, not normalization
--------------------------------------
The adapter records exactly what the payload contains (minus surrounding
whitespace) together with the extraction ``method`` for each value, and **never
invents a value** (Req 2.4, 2.5, 2.6). Canonical formatting (``YYYY-MM`` dates,
``Canonical_Skill_Name`` skills, ISO country codes, ...) is the downstream
Normalize stage's job. A field the payload omits is recorded as a null
:class:`FieldValue`.

LinkedIn is a mid-authority source (priority rank 3, reliability 0.80); both
values come straight from the shared tables in :mod:`.base`.

Robustness (Req 10.3, 18.1): a missing/unreadable file raises :class:`IngestError`
at the ingest boundary (the only exception ``ingest`` may raise); malformed JSON is
detected during ``ingest`` so it fails fast, and ``extract`` is additionally
defensive -- if handed unparseable content it records a structured ``extract``-stage
error rather than crashing.
"""

from __future__ import annotations

import json

from candidate_transformer.models import (
    EducationEntry,
    ErrorReport,
    ExperienceEntry,
    FieldValue,
    Links,
    Location,
    PerSourceRecord,
)

from .base import (
    IngestError,
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)

__all__ = ["LinkedinAdapter"]

SOURCE_TYPE = "linkedin"

# Extraction-method tags recorded for provenance (Req 2.5).
_METHOD_NAME = "linkedin_profile_name"
_METHOD_HEADLINE = "linkedin_profile_headline"
_METHOD_LOCATION = "linkedin_profile_location"
_METHOD_SKILLS = "linkedin_profile_skills"
_METHOD_EXPERIENCE = "linkedin_profile_experience"
_METHOD_EDUCATION = "linkedin_profile_education"
_METHOD_LINKS = "linkedin_profile_url"
_METHOD_EMAIL = "linkedin_profile_email"


class LinkedinAdapter:
    """Adapter for the LinkedIn profile source -- unstructured (Req 1.6).

    Satisfies the :class:`~.base.SourceAdapter` protocol. A single instance is
    stateless and reusable across references.
    """

    source_type: str = SOURCE_TYPE
    reliability: float = reliability_of(SOURCE_TYPE)
    priority: int = priority_of(SOURCE_TYPE)

    # -- recognition -------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True for an explicit ``linkedin`` hint or a ``linkedin.com`` URL location.

        A bare ``.json`` payload is only recognized through the explicit
        ``source_type="linkedin"`` hint so it does not collide with the ATS JSON
        adapter (which claims ``.json`` by extension).
        """
        if ref.source_type == SOURCE_TYPE:
            return True
        if ref.source_type is not None:
            return False
        return "linkedin.com" in ref.location.lower()

    # -- ingest ------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Read the local LinkedIn profile payload (JSON) referenced by ``ref``.

        Operates offline on a fetched/sample payload file: any read/decode failure
        is surfaced as :class:`IngestError` (the only exception ``ingest`` may
        raise) so the runner records an ``ingest``-stage error and continues
        (Req 10.1, 10.3). Malformed JSON is detected here so a corrupt payload fails
        fast. ``source_id`` is ``ref.location`` (Req 1.9).
        """
        try:
            with open(ref.location, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(
                f"failed to read LinkedIn profile payload {ref.location!r}: {exc}"
            ) from exc

        # Validate parseability up front so a malformed payload fails fast.
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"LinkedIn profile payload {ref.location!r} is not valid JSON: {exc}"
            ) from exc

        return RawSource(
            source_id=ref.location,
            source_type=SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    # -- extract -----------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse the profile payload into a single :class:`PerSourceRecord` (Req 2.1).

        A LinkedIn profile describes exactly one candidate, so this always returns
        one record. Fields the payload omits are recorded as null
        :class:`FieldValue` s -- never invented (Req 2.4, 2.6). Malformed content is
        captured as an ``extract``-stage error instead of crashing (Req 10.3, 18.1).
        """
        text = raw.content
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if not isinstance(text, str):
            text = ""

        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            record = PerSourceRecord.empty(raw.source_id, raw.source_type)
            record.errors.append(
                ErrorReport(
                    source=raw.source_id,
                    stage="extract",
                    error=f"Malformed LinkedIn profile payload: {exc}",
                )
            )
            return [record]

        if not isinstance(data, dict):
            record = PerSourceRecord.empty(raw.source_id, raw.source_type)
            record.errors.append(
                ErrorReport(
                    source=raw.source_id,
                    stage="extract",
                    error=f"Expected a JSON object, got {type(data).__name__}",
                )
            )
            return [record]

        values: dict[str, FieldValue] = {}

        # -- name -> full_name --------------------------------------------
        values["full_name"] = FieldValue(
            value=self._clean_str(data.get("name")), method=_METHOD_NAME
        )

        # -- headline -----------------------------------------------------
        values["headline"] = FieldValue(
            value=self._clean_str(data.get("headline")), method=_METHOD_HEADLINE
        )

        # -- location -----------------------------------------------------
        values["location"] = FieldValue(
            value=self._parse_location(data.get("location")),
            method=_METHOD_LOCATION,
        )

        # -- email (optional) ---------------------------------------------
        values["emails"] = FieldValue(
            value=self._clean_str(data.get("email")), method=_METHOD_EMAIL
        )

        # -- skills -------------------------------------------------------
        values["skills"] = FieldValue(
            value=self._extract_skills(data.get("skills")), method=_METHOD_SKILLS
        )

        # -- experience ---------------------------------------------------
        experience = self._extract_experience(data.get("experience"))
        values["experience"] = FieldValue(
            value=experience or None, method=_METHOD_EXPERIENCE
        )

        # -- education ----------------------------------------------------
        education = self._extract_education(data.get("education"))
        values["education"] = FieldValue(
            value=education or None, method=_METHOD_EDUCATION
        )

        # -- profile link -> links.linkedin -------------------------------
        profile_url = self._clean_str(
            data.get("profile_url") or data.get("url") or data.get("html_url")
        )
        values["links"] = FieldValue(
            value=Links(linkedin=profile_url) if profile_url else None,
            method=_METHOD_LINKS,
        )

        return [
            PerSourceRecord(
                source_id=raw.source_id,
                source_type=raw.source_type,
                values=values,
            )
        ]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _clean_str(value: object) -> str | None:
        """Return a whitespace-trimmed non-empty string, else ``None`` (never invents)."""
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @classmethod
    def _extract_skills(cls, value: object) -> list[str] | None:
        """Return the listed skills as a raw, de-duplicated list, or ``None``.

        Skills are recorded verbatim (the Normalize stage maps them to
        ``Canonical_Skill_Name``), de-duplicated case-insensitively with first-seen
        order preserved for determinism (Req 12.2). Never invents a skill (Req 2.6).
        """
        if not isinstance(value, list):
            return None
        seen: set[str] = set()
        skills: list[str] = []
        for item in value:
            cleaned = cls._clean_str(item)
            if cleaned is None:
                continue
            key = cleaned.lower()
            if key not in seen:
                seen.add(key)
                skills.append(cleaned)
        return skills or None

    @classmethod
    def _extract_experience(cls, value: object) -> list[ExperienceEntry]:
        """Parse the experience history into ordered :class:`ExperienceEntry` items.

        Each element is expected to be an object with ``company``/``title`` and
        optional ``start``/``end``/``summary`` (or ``description``). Dates are
        recorded verbatim (e.g. ``"March 2019"``, ``"Present"``); the Normalize
        stage converts them to ``YYYY-MM``. Entry order from the payload is
        preserved.
        """
        if not isinstance(value, list):
            return []
        entries: list[ExperienceEntry] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            entry = ExperienceEntry(
                company=cls._clean_str(item.get("company")),
                title=cls._clean_str(item.get("title")),
                start=cls._clean_str(item.get("start")),
                end=cls._clean_str(item.get("end")),
                summary=cls._clean_str(
                    item.get("summary") or item.get("description")
                ),
            )
            # Skip wholly-empty entries so nothing is invented.
            if any(
                v is not None
                for v in (
                    entry.company,
                    entry.title,
                    entry.start,
                    entry.end,
                    entry.summary,
                )
            ):
                entries.append(entry)
        return entries

    @classmethod
    def _extract_education(cls, value: object) -> list[EducationEntry]:
        """Parse the education history into ordered :class:`EducationEntry` items.

        Each element is expected to be an object with ``institution``,
        ``degree``, ``field`` and an optional numeric ``end_year`` (coerced from a
        string year when needed). Entry order from the payload is preserved; wholly
        empty entries are skipped so nothing is invented.
        """
        if not isinstance(value, list):
            return []
        entries: list[EducationEntry] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            entry = EducationEntry(
                institution=cls._clean_str(item.get("institution")),
                degree=cls._clean_str(item.get("degree")),
                field=cls._clean_str(item.get("field")),
                end_year=cls._coerce_year(item.get("end_year")),
            )
            if any(
                v is not None
                for v in (
                    entry.institution,
                    entry.degree,
                    entry.field,
                    entry.end_year,
                )
            ):
                entries.append(entry)
        return entries

    @staticmethod
    def _coerce_year(value: object) -> int | None:
        """Coerce a year to ``int`` from an int or a digit string, else ``None``."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None

    @classmethod
    def _parse_location(cls, value: object) -> Location | None:
        """Parse a free-text ``"City, Region, Country"`` string into a :class:`Location`.

        LinkedIn stores location as free text. The string is split on commas into
        up to city / region / country; a single token is treated as the city.
        Returns ``None`` when no location is present -- never invents location parts.
        """
        text = cls._clean_str(value)
        if not text:
            return None
        pieces = [p.strip() for p in text.split(",") if p.strip()]
        if not pieces:
            return None
        if len(pieces) >= 3:
            return Location(city=pieces[0], region=pieces[1], country=pieces[2])
        if len(pieces) == 2:
            return Location(city=pieces[0], country=pieces[1])
        return Location(city=pieces[0])

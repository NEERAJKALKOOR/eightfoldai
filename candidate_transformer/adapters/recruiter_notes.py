"""Recruiter notes source adapter (unstructured).

Given a recruiter's free-form **plain-text** notes file (Req 1.8), this adapter
applies lightweight regex extraction to pull out the few signals such notes
reliably contain: emails, phone numbers, the candidate's name, and any mentioned
skills. The design's "Unstructured Source Adapters" entry describes exactly this
("given a plain-text notes file, applies lightweight regex extraction for emails,
phones, names, and mentioned skills").

Recruiter notes are the **least authoritative** source type, so the priority and
reliability come straight from the shared tables in :mod:`.base` (priority rank 5,
reliability 0.60).

Faithful extraction, not normalization
--------------------------------------
The adapter records exactly what the regexes find -- verbatim, minus surrounding
whitespace -- together with the extraction ``method`` for each value, and **never
invents a value** (Req 2.4, 2.5, 2.6). Canonical formatting (E.164 phones,
``Canonical_Skill_Name`` skills, ...) is the downstream Normalize stage's job. A
field the notes do not mention is recorded as a null :class:`FieldValue`.

Robustness (Req 10.2, 18.1): an unreadable file raises :class:`IngestError` at the
ingest boundary; empty content yields a record with all-null values; nothing here
raises during extraction.
"""

from __future__ import annotations

import re

from candidate_transformer.models import FieldValue, PerSourceRecord
from candidate_transformer.normalizers.skills import Controlled_Skill_Vocabulary

from ._text_extract import (
    find_emails,
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

__all__ = ["RecruiterNotesAdapter"]

SOURCE_TYPE = "recruiter_notes"

# Extraction-method tags recorded for provenance (Req 2.5).
_METHOD_EMAIL = "regex_email"
_METHOD_PHONE = "regex_phone"
_METHOD_NAME = "regex_name"
_METHOD_SKILLS = "regex_skill"

# A person-name heuristic: two or three consecutive capitalized words, optionally
# introduced by a cue like "with"/"candidate"/"spoke with". Kept deliberately
# conservative so common noise (single capitalized words, ALL-CAPS headers) does
# not masquerade as a name. The first match in document order wins.
_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
)
# Words that look like a Title-case name but are obviously not one.
_NAME_STOPWORDS = {
    "call notes",
    "tech lead",
    "good culture",
    "culture fit",
}


class RecruiterNotesAdapter:
    """Adapter for the Recruiter notes source (plain text) -- unstructured (Req 1.8).

    Satisfies the :class:`~.base.SourceAdapter` protocol. A single instance is
    stateless and reusable across references.
    """

    source_type: str = SOURCE_TYPE
    reliability: float = reliability_of(SOURCE_TYPE)
    priority: int = priority_of(SOURCE_TYPE)

    # -- recognition -------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True for an explicit ``recruiter_notes`` hint or a ``.txt`` location."""
        if ref.source_type == SOURCE_TYPE:
            return True
        if ref.source_type is not None:
            return False
        return ref.location.lower().endswith(".txt")

    # -- ingest ------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Read the notes file's text.

        Raises :class:`IngestError` (and only :class:`IngestError`) on any read or
        decode failure so the runner records an ``ingest``-stage error and
        continues (Req 10.1). ``source_id`` is ``ref.location`` (Req 1.9).
        """
        try:
            with open(ref.location, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(
                f"failed to read recruiter notes {ref.location!r}: {exc}"
            ) from exc

        return RawSource(
            source_id=ref.location,
            source_type=SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    # -- extract -----------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse the notes text into a single :class:`PerSourceRecord` (Req 2.1).

        Always returns exactly one record. Absent signals are recorded as null
        :class:`FieldValue` s -- never invented (Req 2.4, 2.6).
        """
        text = raw.content
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if not isinstance(text, str):
            text = ""

        emails = find_emails(text)
        phones = find_phones(text)
        skills = find_skill_mentions(text, Controlled_Skill_Vocabulary)

        values: dict[str, FieldValue] = {
            "full_name": FieldValue(
                value=self._extract_name(text), method=_METHOD_NAME
            ),
            # Single scalar per source; Merge combines + dedups across sources.
            "emails": FieldValue(
                value=emails[0] if emails else None, method=_METHOD_EMAIL
            ),
            "phones": FieldValue(
                value=phones[0] if phones else None, method=_METHOD_PHONE
            ),
            "skills": FieldValue(value=skills or None, method=_METHOD_SKILLS),
        }

        return [
            PerSourceRecord(
                source_id=raw.source_id,
                source_type=raw.source_type,
                values=values,
            )
        ]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_name(text: str) -> str | None:
        """Return the first plausible person name in ``text``, or ``None``.

        Scans for the first run of two or three consecutive capitalized words,
        skipping a small stop-list of Title-case phrases that are clearly not
        names (e.g. ``"Call notes"``, ``"Tech Lead"``). Never invents a name.
        """
        if not isinstance(text, str) or not text:
            return None
        for match in _NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate.lower() in _NAME_STOPWORDS:
                continue
            return candidate
        return None

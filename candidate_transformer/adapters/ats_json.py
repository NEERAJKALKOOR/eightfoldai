"""ATS JSON source adapter (structured).

The ATS (applicant-tracking-system) export is a *structured* source whose field
names deliberately differ from the Canonical_Schema (Req 1.4, 2.3). For example
the ATS calls the candidate's name ``candidateName`` where the canonical schema
calls it ``full_name``. This adapter bridges that gap with a single, auditable,
**declarative field-mapping table** (:data:`ATS_FIELD_MAP`) that translates ATS
keys to canonical field paths -- the heart of Requirement 2.3.

Design boundary: this module only knows how to read and parse an ATS JSON file
into one or more :class:`~candidate_transformer.models.PerSourceRecord`. It does
*not* normalize values (phones stay as written, skills stay as raw aliases) --
normalization, identity resolution, and merge are downstream stages. Every value
is recorded verbatim together with the *extraction method* identifying which ATS
key it came from (e.g. ``"ats_field:candidateName"``), so each value remains
traceable (Req 2.5). Absent keys are simply omitted -- the adapter never invents
a value (Req 2.4, 2.6).

Robustness (Req 10.3, 18.1): a missing/unreadable file raises :class:`IngestError`
at the ``ingest`` boundary (the only exception ``ingest`` is allowed to raise);
malformed JSON is detected during ``ingest`` so it fails fast, and ``extract`` is
additionally defensive -- if it is ever handed unparseable content it records a
structured ``extract``-stage error on a per-source record instead of crashing the
process.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from candidate_transformer.models import (
    ErrorReport,
    ExperienceEntry,
    FieldValue,
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

__all__ = ["AtsJsonAdapter", "AtsFieldMapping", "ATS_FIELD_MAP"]

SOURCE_TYPE = "ats_json"


# ---------------------------------------------------------------------------
# Declarative field-mapping table (Req 2.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AtsFieldMapping:
    """One row of the ATS-key -> canonical-path translation table.

    ``ats_key`` is the source field name as it appears in the ATS JSON.
    ``targets`` is the tuple of canonical destinations the value flows to. A value
    may feed more than one canonical destination (e.g. the ATS ``jobTitle`` becomes
    both the candidate ``headline`` and an ``experience[].title``).

    Supported canonical-path forms (kept intentionally small and auditable):

    * ``"full_name"``           -- a scalar canonical field. Single-valued
      per-source fields (including ``emails``/``phones``, of which an ATS record
      carries exactly one each) are recorded as a scalar value; the Merge stage
      collects these across a candidate's sources and dedups them into the
      canonical list, mirroring the Recruiter CSV adapter's approach.
    * ``"skills[]"``            -- a genuinely list-valued source field
      (``skillList``); every entry is captured as a list.
    * ``"experience[].company"`` -- a subfield of the (single) experience entry the
      ATS record describes (the candidate's current role).
    * ``"location.city"`` / ``"location.region"`` / ``"location.country"`` -- a
      subfield of the (single) :class:`~candidate_transformer.models.Location`
      assembled from the ATS's city/region/state/country keys.
    """

    ats_key: str
    targets: tuple[str, ...]


# The translation table from ATS field names to Canonical_Schema paths (Req 2.3).
# This is the single place to audit/extend how a non-canonical ATS export maps onto
# the canonical record. Keeping it declarative means adding a new ATS field is a
# one-line change with no parsing-logic edits.
ATS_FIELD_MAP: tuple[AtsFieldMapping, ...] = (
    AtsFieldMapping("candidateName", ("full_name",)),
    AtsFieldMapping("emailAddress", ("emails",)),
    AtsFieldMapping("phoneNumber", ("phones",)),
    AtsFieldMapping("currentEmployer", ("experience[].company",)),
    AtsFieldMapping("jobTitle", ("headline", "experience[].title")),
    AtsFieldMapping("yrsExp", ("years_experience",)),
    AtsFieldMapping("skillList", ("skills[]",)),
    AtsFieldMapping("city", ("location.city",)),
    AtsFieldMapping("region", ("location.region",)),
    AtsFieldMapping("state", ("location.region",)),
    AtsFieldMapping("country", ("location.country",)),
)


def _method_for(ats_key: str) -> str:
    """The provenance extraction method for a value taken from ``ats_key`` (Req 2.5)."""
    return f"ats_field:{ats_key}"


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class AtsJsonAdapter:
    """Reads an ATS JSON export into per-source records (Req 1.4, 2.1-2.6).

    Implements the :class:`~candidate_transformer.adapters.base.SourceAdapter`
    protocol. The JSON may be either a top-level array of candidate objects (one
    :class:`PerSourceRecord` per element) or a single candidate object (one record).
    """

    source_type: str = SOURCE_TYPE
    reliability: float = reliability_of(SOURCE_TYPE)
    priority: int = priority_of(SOURCE_TYPE)

    # -- recognition --------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True when the reference is explicitly ``ats_json`` or ends with ``.json``."""
        if ref.source_type == SOURCE_TYPE:
            return True
        if ref.source_type is not None:
            # An explicit hint for a different source type means this is not ours.
            return False
        return ref.location.lower().endswith(".json")

    # -- ingest -------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Load the JSON file text, raising only :class:`IngestError` on failure.

        Malformed JSON is detected here so the source fails fast at the ingest
        boundary; any read/IO/decoding problem is also surfaced as
        :class:`IngestError` (Req 10.1, 10.3).
        """
        try:
            with open(ref.location, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(
                f"Could not read ATS JSON source {ref.location!r}: {exc}"
            ) from exc

        # Validate parseability up front so a malformed export fails fast.
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"ATS JSON source {ref.location!r} is not valid JSON: {exc}"
            ) from exc

        return RawSource(
            source_id=ref.location,
            source_type=SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    # -- extract ------------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse ``raw`` into one or more per-source records (Req 2.1).

        Supports both a top-level JSON array (one record per element) and a single
        JSON object (one record). Defensive against malformed content: rather than
        crash the process it returns a single record carrying an ``extract``-stage
        :class:`ErrorReport` (Req 10.3, 18.1).
        """
        text = raw.content if isinstance(raw.content, str) else raw.content.decode(
            "utf-8", errors="replace"
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            record = PerSourceRecord.empty(raw.source_id, raw.source_type)
            record.errors.append(
                ErrorReport(
                    source=raw.source_id,
                    stage="extract",
                    error=f"Malformed ATS JSON: {exc}",
                )
            )
            return [record]

        if isinstance(data, list):
            objects = data
            indexed = True
        else:
            objects = [data]
            indexed = False

        records: list[PerSourceRecord] = []
        for index, obj in enumerate(objects):
            source_id = f"{raw.source_id}#{index}" if indexed else raw.source_id
            records.append(self._record_from_object(obj, source_id, raw.source_type))
        return records

    # -- helpers ------------------------------------------------------------

    def _record_from_object(
        self, obj: object, source_id: str, source_type: str
    ) -> PerSourceRecord:
        """Build one :class:`PerSourceRecord` from a single ATS candidate object.

        Non-object array elements (e.g. a stray string) are not crashed on; they
        produce an ``extract``-stage error on an otherwise empty record.
        """
        record = PerSourceRecord(source_id=source_id, source_type=source_type)

        if not isinstance(obj, dict):
            record.errors.append(
                ErrorReport(
                    source=source_id,
                    stage="extract",
                    error=f"Expected a JSON object, got {type(obj).__name__}",
                )
            )
            return record

        # Accumulates the single experience entry's subfields and the ATS keys that
        # contributed them, so the entry can carry combined provenance.
        experience_fields: dict[str, object] = {}
        experience_keys: list[str] = []
        # Likewise for the (single) location object assembled from city/region/country.
        location_fields: dict[str, object] = {}
        location_keys: list[str] = []

        for mapping in ATS_FIELD_MAP:
            if mapping.ats_key not in obj:
                # Absent key -> omit; never invent a value (Req 2.4, 2.6).
                continue
            raw_value = obj[mapping.ats_key]
            if raw_value is None:
                # Present-but-null behaves like absent: do not record an invented value.
                continue

            method = _method_for(mapping.ats_key)
            for target in mapping.targets:
                if target.endswith("[]"):
                    # Genuinely list-valued source field (skillList): record all
                    # entries as a list so every skill is captured.
                    field_name = target[:-2]
                    values = raw_value if isinstance(raw_value, list) else [raw_value]
                    values = [v for v in values if v is not None]
                    record.values[field_name] = FieldValue(value=values, method=method)
                elif target.startswith("experience[]."):
                    subfield = target.split(".", 1)[1]
                    experience_fields[subfield] = raw_value
                    experience_keys.append(mapping.ats_key)
                elif target.startswith("location."):
                    subfield = target.split(".", 1)[1]
                    location_fields[subfield] = raw_value
                    location_keys.append(mapping.ats_key)
                else:
                    # Scalar canonical field (full_name, headline, years_experience).
                    record.values[target] = FieldValue(value=raw_value, method=method)

        if experience_fields:
            entry = ExperienceEntry(
                company=experience_fields.get("company"),
                title=experience_fields.get("title"),
            )
            # Combined provenance: the experience entry was assembled from one or more
            # ATS keys (currentEmployer and/or jobTitle).
            combined_method = "ats_field:" + "+".join(dict.fromkeys(experience_keys))
            record.values["experience"] = FieldValue(
                value=[entry], method=combined_method
            )

        if location_fields:
            location = Location(
                city=location_fields.get("city"),
                region=location_fields.get("region"),
                country=location_fields.get("country"),
            )
            # Combined provenance: the location was assembled from one or more ATS
            # keys (city/region/state/country). Normalization (country -> ISO-3166)
            # happens downstream in the Normalize stage.
            combined_method = "ats_field:" + "+".join(dict.fromkeys(location_keys))
            record.values["location"] = FieldValue(
                value=location, method=combined_method
            )

        return record

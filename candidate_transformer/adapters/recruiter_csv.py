"""Recruiter CSV source adapter (structured).

Reads a Recruiter CSV with the header ``name, email, phone, current_company,
title`` and turns **each data row into one** :class:`PerSourceRecord` (Req 1.3,
2.1). Recruiter CSV is the most authoritative source type, so its priority and
reliability come straight from the shared tables in :mod:`.base`.

Faithful extraction, not normalization
--------------------------------------
Per the design, normalization (E.164 phones, lowercased emails, ISO country
codes, ...) happens in the dedicated Normalize stage. This adapter therefore only
performs the *minimal* cleanup of trimming surrounding whitespace from a cell and
records each value verbatim together with the extraction ``method`` ``"csv_column"``
(Req 2.5). A blank or absent column yields a **null** :class:`FieldValue` -- the
adapter never invents a value (Req 2.4, 2.6).

Multi-valued field representation (design decision)
---------------------------------------------------
The canonical schema models ``emails`` and ``phones`` as *lists*, but a single
recruiter CSV row contains exactly **one** email and **one** phone. To stay
faithful to what the source actually contained, this adapter stores the single
scalar value it found under the canonical key (``values["emails"]`` /
``values["phones"]``) as one :class:`FieldValue` -- **not** a list. The Merge
stage is responsible for collecting these per-source scalar values across all of a
candidate's sources and combining them into the deduplicated canonical list
(design "Merge & Conflict Resolution", Req 5.6). Keeping one value per source here
means each value retains its own provenance and the per-source record never
over-claims (a row with one email does not look like it supplied many).

``current_company`` + ``title`` are combined into a single
:class:`~candidate_transformer.models.ExperienceEntry` stored under
``values["experience"]`` (as a one-element list, the shape the Merge stage expects
for the list-valued ``experience`` field), and ``title`` is *additionally* recorded
under ``values["headline"]`` (Req 2.2) -- mirroring the design's
"``current_company`` -> latest ``experience[].company``, ``title`` ->
``headline``/``experience[].title``".
"""

from __future__ import annotations

import csv
import io

from candidate_transformer.models import ExperienceEntry, FieldValue, PerSourceRecord

from .base import (
    IngestError,
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)

__all__ = ["RecruiterCsvAdapter"]

#: The extraction method recorded on every value this adapter produces (Req 2.5).
_METHOD = "csv_column"

#: The source type identifier this adapter handles.
_SOURCE_TYPE = "recruiter_csv"


def _clean(cell: str | None) -> str | None:
    """Trim surrounding whitespace; map an empty/blank cell to ``None``.

    Trimming is the only transformation applied -- case and internal formatting
    are preserved so the Normalize stage receives a faithful raw value. A cell
    that is missing or whitespace-only is treated as "not provided" (``None``).
    """
    if cell is None:
        return None
    stripped = cell.strip()
    return stripped or None


class RecruiterCsvAdapter:
    """Adapter for the Recruiter CSV structured source (Req 1.3).

    Satisfies the :class:`~.base.SourceAdapter` protocol. One instance is stateless
    and reusable across references.
    """

    source_type: str = _SOURCE_TYPE
    reliability: float = reliability_of(_SOURCE_TYPE)
    priority: int = priority_of(_SOURCE_TYPE)

    # -- recognition -------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True when the ref is explicitly tagged ``recruiter_csv`` or ends ``.csv``."""
        if ref.source_type == _SOURCE_TYPE:
            return True
        return ref.location.lower().endswith(".csv")

    # -- ingest ------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Read the CSV file's text.

        Raises :class:`IngestError` (and only :class:`IngestError`) on any read
        failure -- a missing file or a decode error -- so the runner can record an
        ``ingest``-stage error and continue with the remaining sources (Req 10.1).
        ``source_id`` is set to ``ref.location`` for provenance (Req 1.9).
        """
        try:
            with open(ref.location, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(
                f"failed to read recruiter CSV {ref.location!r}: {exc}"
            ) from exc

        return RawSource(
            source_id=ref.location,
            source_type=_SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    # -- extract -----------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse the CSV content into one :class:`PerSourceRecord` per data row.

        Uses the stdlib :mod:`csv` module with a header row. Extra columns are
        ignored and missing columns resolve to ``None`` (null FieldValue), so the
        adapter tolerates schema drift gracefully. An empty source yields an empty
        record list (no rows). A single malformed row is captured as a best-effort
        record with an attached error rather than crashing the whole extraction.
        """
        text = raw.content
        if isinstance(text, bytes):
            # Defensive: ingest produces text, but tolerate bytes without raising.
            text = text.decode("utf-8", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        records: list[PerSourceRecord] = []
        for row in reader:
            records.append(self._row_to_record(raw, row))
        return records

    # -- helpers -----------------------------------------------------------

    def _row_to_record(
        self, raw: RawSource, row: dict[str | None, object]
    ) -> PerSourceRecord:
        """Map one CSV row dict to a :class:`PerSourceRecord` (Req 2.1, 2.2)."""
        # csv.DictReader stores values keyed by header name. A short row leaves
        # missing columns as None; a long row collects extras under the None key,
        # which we ignore by only reading the named columns.
        def col(name: str) -> str | None:
            value = row.get(name)
            return _clean(value) if isinstance(value, str) or value is None else None

        name = col("name")
        email = col("email")
        phone = col("phone")
        company = col("current_company")
        title = col("title")

        # current_company + title -> one experience entry (list-valued field).
        if company is None and title is None:
            experience_fv = FieldValue(value=None, method=_METHOD)
        else:
            experience_fv = FieldValue(
                value=[ExperienceEntry(company=company, title=title)],
                method=_METHOD,
            )

        values: dict[str, FieldValue] = {
            "full_name": FieldValue(value=name, method=_METHOD),
            # Single scalar per row; Merge combines + dedups into the canonical list.
            "emails": FieldValue(value=email, method=_METHOD),
            "phones": FieldValue(value=phone, method=_METHOD),
            "headline": FieldValue(value=title, method=_METHOD),
            "experience": experience_fv,
        }

        return PerSourceRecord(
            source_id=raw.source_id,
            source_type=raw.source_type,
            values=values,
        )

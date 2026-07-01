"""SourceAdapter interface, source reference/raw types, and the priority tables.

This module defines the *contract* every source adapter implements, plus the two
data structures that flow into an adapter (:class:`SourceRef` -> :class:`RawSource`)
and the two module-level lookup tables (:data:`SOURCE_RELIABILITY` and
:data:`SOURCE_PRIORITY`) that encode the fixed ranking and reliability weights from
the design.

Design boundary: the pipeline core knows nothing about CSV parsing, PDF extraction,
or URL scraping. It only knows about this interface and the ``PerSourceRecord`` an
adapter emits, which is what makes new sources pluggable (Req 1, 2).

Provenance recording convention (Req 1.9, 2.5):
    * Every :class:`RawSource` carries a ``source_id`` (a unique reference to the
      originating artifact, e.g. the file path or URL) and a ``source_type`` (one of
      the known :data:`~candidate_transformer.models.per_source.SourceType` values).
      These two values are copied verbatim onto every ``PerSourceRecord`` an adapter
      produces, so each canonical value can later be traced back to the exact source
      it came from.
    * Every extracted value is wrapped in a ``FieldValue`` whose ``method`` records
      the *extraction method* used to derive it (e.g. ``"csv_column"``,
      ``"regex_email"``, ``"pdf_section_skills"``). Missing fields are set to null and
      never invented (Req 2.4, 2.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from candidate_transformer.models import PerSourceRecord, SourceType

__all__ = [
    "SourceRef",
    "RawSource",
    "IngestError",
    "SourceAdapter",
    "SOURCE_PRIORITY",
    "SOURCE_RELIABILITY",
    "priority_of",
    "reliability_of",
]


# ---------------------------------------------------------------------------
# Source reference / raw content
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    """A reference to an input artifact, before it is loaded (Req 1.1).

    ``location`` is the path or URL identifying the artifact. ``source_type`` is an
    optional explicit hint; when provided it lets a caller force a particular adapter
    (and short-circuit content sniffing). When ``None``, the registry relies on each
    adapter's :meth:`SourceAdapter.can_handle` to recognize the reference.
    """

    location: str
    source_type: SourceType | None = None


@dataclass(frozen=True)
class RawSource:
    """Loaded raw content for one source (Req 1.1, 1.9).

    Produced by :meth:`SourceAdapter.ingest`. ``content`` holds the raw bytes or
    decoded text of the artifact. ``source_id`` and ``source_type`` are the
    provenance anchors copied onto every resulting ``PerSourceRecord`` so each value
    remains traceable to its origin.
    """

    source_id: str
    source_type: SourceType
    content: bytes | str
    # The originating reference, retained for diagnostics / re-reads.
    ref: SourceRef | None = field(default=None)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IngestError(Exception):
    """Raised by :meth:`SourceAdapter.ingest` when raw content cannot be loaded.

    This is the *only* exception type ``ingest`` is permitted to raise. The pipeline
    runner catches it at the ingest boundary, converts it into a structured
    ``ErrorReport`` tagged with the ``ingest`` stage, and continues the run with the
    remaining sources (Req 10.1, 10.5).
    """


# ---------------------------------------------------------------------------
# The adapter interface
# ---------------------------------------------------------------------------


@runtime_checkable
class SourceAdapter(Protocol):
    """The uniform interface every source type implements (Req 1, 2.3).

    Attributes
    ----------
    source_type:
        One of the known source type identifiers, e.g. ``"recruiter_csv"``.
    reliability:
        The :data:`SOURCE_RELIABILITY` weight for this source type, in ``[0, 1]``,
        used as an input to confidence scoring (Req 7.3).
    priority:
        The :data:`SOURCE_PRIORITY` rank for this source type. Lower means more
        authoritative; it drives the Winner_Selection_Policy (Req 5.4).
    """

    source_type: str
    reliability: float
    priority: int

    def can_handle(self, ref: SourceRef) -> bool:
        """Return ``True`` if this adapter recognizes ``ref``.

        Recognition is based on the explicit ``ref.source_type`` hint when present,
        otherwise on cues such as file extension or URL host.
        """
        ...

    def ingest(self, ref: SourceRef) -> RawSource:
        """Load the artifact referenced by ``ref`` into a :class:`RawSource`.

        Raises :class:`IngestError` (and only :class:`IngestError`) on failure so the
        runner can record it as an ``ingest``-stage error and continue (Req 10.1).
        """
        ...

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse ``raw`` into one or more :class:`PerSourceRecord`.

        Structured sources may yield many records (one per CSV row); unstructured
        sources yield one. Missing fields are set to null and never invented; each
        value records the extraction ``method`` used to derive it (Req 2.4, 2.5, 2.6).
        """
        ...


# ---------------------------------------------------------------------------
# SourcePriority / Source_Reliability tables
# ---------------------------------------------------------------------------

# SourcePriority (Req 5.4): the fixed ranking from most to least authoritative.
# Index position is the priority rank (lower index = lower `priority` int = more
# authoritative). This ordering also encodes the default registration order of the
# AdapterRegistry.
SOURCE_PRIORITY: tuple[SourceType, ...] = (
    "recruiter_csv",   # 0 - most authoritative
    "ats_json",        # 1
    "resume",          # 2
    "linkedin",        # 3
    "github",          # 4
    "recruiter_notes",  # 5 - least authoritative
)

# Source_Reliability (Req 7.3): fixed numeric weight per source type, in [0, 1].
SOURCE_RELIABILITY: dict[SourceType, float] = {
    "recruiter_csv": 0.95,
    "ats_json": 0.90,
    "resume": 0.85,
    "linkedin": 0.80,
    "github": 0.70,
    "recruiter_notes": 0.60,
}


def priority_of(source_type: str) -> int:
    """Return the SourcePriority rank for ``source_type`` (lower = more authoritative).

    Unknown source types sort *after* all known ones (least authoritative), keeping
    ordering total and deterministic.
    """
    try:
        return SOURCE_PRIORITY.index(source_type)  # type: ignore[arg-type]
    except ValueError:
        return len(SOURCE_PRIORITY)


def reliability_of(source_type: str) -> float:
    """Return the Source_Reliability weight for ``source_type`` (0.0 if unknown)."""
    return SOURCE_RELIABILITY.get(source_type, 0.0)  # type: ignore[arg-type]

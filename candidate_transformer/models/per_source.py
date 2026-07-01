"""Intermediate per-source record data structures.

Implements :class:`FieldValue` (``{value, method, normalization_quality}``) and
:class:`PerSourceRecord` (``{source_id, source_type, values, errors}``) from the
design's Data Models section. A per-source record isolates the contribution of a
single source so that every value remains traceable (Req 2.1, 2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .reporting import ErrorReport

# The known source types (mirrors the SourcePriority ordering in the design).
SourceType = Literal[
    "recruiter_csv",
    "ats_json",
    "resume",
    "linkedin",
    "github",
    "recruiter_notes",
]


@dataclass
class FieldValue:
    """One extracted/normalized value for a canonical field.

    ``value`` is the (possibly null) value, ``method`` is the extraction method
    used to derive it (for provenance, Req 2.5), and ``normalization_quality`` is
    the Normalization_Quality score in ``[0.0, 1.0]`` (Req 3.10) feeding the
    confidence formula.
    """

    value: Any = None
    method: str | None = None
    normalization_quality: float = 0.0


@dataclass
class PerSourceRecord:
    """The intermediate record produced for a single source (Req 2.1).

    ``values`` maps a canonical field name to a :class:`FieldValue`. Fields a
    source does not provide are simply absent or carry a null-valued
    :class:`FieldValue` (Req 2.4, 2.6). ``errors`` collects any
    :class:`ErrorReport` raised while processing the source.
    """

    source_id: str
    source_type: str
    values: dict[str, FieldValue] = field(default_factory=dict)
    errors: list[ErrorReport] = field(default_factory=list)

    @classmethod
    def empty(cls, source_id: str, source_type: str) -> "PerSourceRecord":
        """Return an all-null per-source record (no values, no errors) (Req 10.2)."""
        return cls(source_id=source_id, source_type=source_type)


def new_null_per_source_record(source_id: str, source_type: str) -> PerSourceRecord:
    """Construction helper yielding an all-null :class:`PerSourceRecord` (Req 10.2)."""
    return PerSourceRecord.empty(source_id=source_id, source_type=source_type)

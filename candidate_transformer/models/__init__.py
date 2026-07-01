"""Core data models.

Defines the canonical record, per-source record, error report, log entry, and
run result data structures used across the pipeline, plus construction helpers
for all-null records and deterministic JSON-ready serialization.
"""

from __future__ import annotations

from .canonical import (
    Canonical_Record,
    CanonicalRecord,
    EducationEntry,
    ExperienceEntry,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
    new_null_canonical_record,
)
from .per_source import (
    FieldValue,
    PerSourceRecord,
    SourceType,
    new_null_per_source_record,
)
from .reporting import ErrorReport, LogEntry, LogLevel, Stage
from .run_result import RunResult
from .serialization import (
    canonical_record_to_dict,
    run_result_to_dict,
    to_dict,
)

__all__ = [
    # Canonical record + sub-structures
    "CanonicalRecord",
    "Canonical_Record",
    "Location",
    "Links",
    "Skill",
    "ExperienceEntry",
    "EducationEntry",
    "ProvenanceEntry",
    "new_null_canonical_record",
    # Per-source record
    "FieldValue",
    "PerSourceRecord",
    "SourceType",
    "new_null_per_source_record",
    # Reporting
    "ErrorReport",
    "LogEntry",
    "Stage",
    "LogLevel",
    # Run result
    "RunResult",
    # Serialization
    "to_dict",
    "canonical_record_to_dict",
    "run_result_to_dict",
]

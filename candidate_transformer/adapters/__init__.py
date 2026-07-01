"""Source adapters and adapter registry.

Each source type (Recruiter CSV, ATS JSON, GitHub, LinkedIn, Resume, Recruiter
notes) is read through a uniform adapter interface. Adapters are registered in a
fixed-order registry that also encodes default source priority.

This package currently exposes the adapter *contract* and registry only; concrete
adapters are added in later tasks and register themselves against the registry.
"""

from __future__ import annotations

from .base import (
    SOURCE_PRIORITY,
    SOURCE_RELIABILITY,
    IngestError,
    RawSource,
    SourceAdapter,
    SourceRef,
    priority_of,
    reliability_of,
)
from .ats_json import AtsJsonAdapter
from .github import GithubAdapter
from .linkedin import LinkedinAdapter
from .recruiter_csv import RecruiterCsvAdapter
from .recruiter_notes import RecruiterNotesAdapter
from .registry import AdapterRegistry, NoAdapterFoundError
from .resume import ResumeAdapter

__all__ = [
    # Interface + types
    "SourceAdapter",
    "SourceRef",
    "RawSource",
    "IngestError",
    # Tables
    "SOURCE_PRIORITY",
    "SOURCE_RELIABILITY",
    "priority_of",
    "reliability_of",
    # Registry
    "AdapterRegistry",
    "NoAdapterFoundError",
    # Concrete adapters
    "RecruiterCsvAdapter",
    "AtsJsonAdapter",
    "ResumeAdapter",
    "RecruiterNotesAdapter",
    "GithubAdapter",
    "LinkedinAdapter",
    "build_default_registry",
]


def build_default_registry() -> AdapterRegistry:
    """Return an :class:`AdapterRegistry` pre-populated with the available adapters.

    Registers the structured adapters (:class:`RecruiterCsvAdapter`,
    :class:`AtsJsonAdapter`) and the unstructured adapters (:class:`ResumeAdapter`,
    :class:`RecruiterNotesAdapter`, :class:`GithubAdapter`,
    :class:`LinkedinAdapter`); further adapters register themselves here as they
    are implemented. This factory lets callers obtain a ready-to-use registry
    without wiring the full engine.
    """
    return AdapterRegistry(
        [
            RecruiterCsvAdapter(),
            AtsJsonAdapter(),
            ResumeAdapter(),
            RecruiterNotesAdapter(),
            GithubAdapter(),
            LinkedinAdapter(),
        ]
    )

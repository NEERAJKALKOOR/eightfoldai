"""Canonical record data structures (Canonical_Schema).

Implements the internal, fixed-schema representation of one candidate before
projection, exactly as described in the design's Data Models section. Every
structure is a typed :mod:`dataclasses` dataclass with sensible defaults and
construction helpers.

Design principle: *a wrong-but-confident value is worse than an honestly-empty
one*. The :func:`new_null_canonical_record` factory yields a record with every
scalar field ``None``, every list empty, and ``overall_confidence`` ``0.0`` so a
run can always produce a structurally valid record even when no source supplies
any value (Req 10.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Location:
    """Canonical location sub-structure.

    ``country`` is an ISO-3166-alpha-2 code when resolvable, else ``None``.
    """

    city: str | None = None
    region: str | None = None
    country: str | None = None

    @classmethod
    def empty(cls) -> "Location":
        """Return an all-null location."""
        return cls()


@dataclass
class Links:
    """Canonical links sub-structure.

    ``other`` holds any additional profile links not covered by the named
    fields, kept as a (deterministically ordered, deduplicated) list.
    """

    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Links":
        """Return an all-null links structure with an empty ``other`` list."""
        return cls()


@dataclass
class Skill:
    """A single canonical skill.

    ``name`` is a Canonical_Skill_Name, ``confidence`` is the Field_Confidence in
    ``[0.0, 1.0]``, and ``sources`` lists the source ids that contributed it.
    """

    name: str | None = None
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)


@dataclass
class ExperienceEntry:
    """A single work-experience entry. ``start``/``end`` are ``YYYY-MM`` or null."""

    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    summary: str | None = None


@dataclass
class EducationEntry:
    """A single education entry. ``end_year`` is a numeric year or null."""

    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    end_year: int | None = None


@dataclass
class ProvenanceEntry:
    """A provenance record for one canonical value (Req 6.1).

    Shape ``{ field, value, source, method, confidence }``. When a field is null
    because no source provided it, ``value`` is ``None`` and ``source``/``method``
    indicate the value was not found (Req 6.3).
    """

    field: str
    value: Any = None
    source: str | None = None
    method: str | None = None
    confidence: float = 0.0


@dataclass
class CanonicalRecord:
    """The internal, fixed-schema representation of one candidate (Canonical_Schema).

    Use :func:`new_null_canonical_record` to obtain an all-null record (Req 10.4).
    """

    candidate_id: str | None = None
    full_name: str | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    location: Location = field(default_factory=Location)
    links: Links = field(default_factory=Links)
    headline: str | None = None
    years_experience: float | None = None
    skills: list[Skill] = field(default_factory=list)
    # Extracted skill strings that did not match the controlled vocabulary. They
    # are surfaced honestly (rather than dropped) so no data is silently lost; if
    # the vocabulary is later expanded, these become recognized skills with no
    # code change.
    unknown_skills: list[str] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    provenance: list[ProvenanceEntry] = field(default_factory=list)
    overall_confidence: float = 0.0

    @classmethod
    def empty(cls, candidate_id: str | None = None) -> "CanonicalRecord":
        """Return an all-null canonical record (Req 10.4).

        Every scalar field is ``None``, every list-valued field is empty, nested
        structures are all-null, and ``overall_confidence`` is ``0.0``.
        """
        return cls(candidate_id=candidate_id)


# Backwards/spec-name alias: the requirements refer to ``Canonical_Record``.
Canonical_Record = CanonicalRecord


def new_null_canonical_record(candidate_id: str | None = None) -> CanonicalRecord:
    """Construction helper yielding an all-null :class:`CanonicalRecord` (Req 10.4)."""
    return CanonicalRecord.empty(candidate_id=candidate_id)

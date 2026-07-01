"""Property-based test for projection isolation (no mutation).

Feature: candidate-data-transformer, Property 3

Property 3: Projection isolation (no mutation).

    For any Canonical_Record and any (one or more) Projection_Configs, projecting
    leaves the canonical record deep-equal to its pre-projection state, and applying
    multiple configs produces each Projected_Profile from the same unchanged
    canonical record.

**Validates: Requirements 8.2, 15.1, 15.2, 15.3**

The test generates a varied canonical record and one or more projection configs,
deep-copies the record before any projection, then:

* projects every config sequentially against the same record and asserts the record
  remains deep-equal to its pre-projection snapshot -- the projection layer never
  mutates the canonical record (Req 8.2, 15.1, 15.3); and
* asserts that each config, projected against the (possibly already-used) shared
  record, yields a profile identical to projecting the same config against a fresh
  deep copy of the original record -- so every projection is produced from the same
  unchanged canonical record regardless of how many configs precede it (Req 15.2).
"""

from __future__ import annotations

import copy
import string
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.engine.projection import ProjectionConfig, project
from candidate_transformer.models.canonical import (
    CanonicalRecord,
    EducationEntry,
    ExperienceEntry,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
)

# Canonical ``from`` paths covering every supported path shape: plain scalar,
# nested (``location.city``), indexed (``phones[0]``), nested scalar under links,
# array projection (``skills[].name``), and a whole list-valued field.
_CANDIDATE_PATHS = [
    "candidate_id",
    "full_name",
    "headline",
    "years_experience",
    "emails[0]",
    "phones[0]",
    "location.city",
    "location.region",
    "location.country",
    "links.linkedin",
    "links.github",
    "links.portfolio",
    "skills[].name",
    "experience",
    "education",
]

# Per-field normalization transforms accepted by the projection engine (Req 8.10),
# plus ``None`` for "no normalization".
_NORMALIZE_CHOICES = [None, "lowercase", "uppercase"]

# Missing-value behaviors: isolation must hold regardless of which is chosen, so we
# exercise all three (null / omit / error) across generated configs.
_ON_MISSING_CHOICES = ["null", "omit", "error"]

_text = st.text(
    alphabet=string.ascii_letters + string.digits + " .-_@", min_size=0, max_size=12
)
_opt_text = st.one_of(st.none(), _text)


def _skill_strategy() -> st.SearchStrategy[Skill]:
    """A Skill with a possibly-null name and a small confidence / sources payload."""
    return st.builds(
        Skill,
        name=_opt_text,
        confidence=st.floats(min_value=0.0, max_value=1.0),
        sources=st.lists(_text, max_size=3),
    )


def _experience_strategy() -> st.SearchStrategy[ExperienceEntry]:
    return st.builds(
        ExperienceEntry,
        company=_opt_text,
        title=_opt_text,
        start=_opt_text,
        end=_opt_text,
        summary=_opt_text,
    )


def _education_strategy() -> st.SearchStrategy[EducationEntry]:
    return st.builds(
        EducationEntry,
        institution=_opt_text,
        degree=_opt_text,
        field=_opt_text,
        end_year=st.one_of(st.none(), st.integers(min_value=1950, max_value=2030)),
    )


def _provenance_strategy() -> st.SearchStrategy[ProvenanceEntry]:
    return st.builds(
        ProvenanceEntry,
        field=_text,
        value=st.one_of(st.none(), _text),
        source=_opt_text,
        method=_opt_text,
        confidence=st.floats(min_value=0.0, max_value=1.0),
    )


def _record_strategy() -> st.SearchStrategy[CanonicalRecord]:
    """Generate canonical records with varied populated / absent fields.

    Covers every field referenced by ``_CANDIDATE_PATHS`` plus provenance, so both
    the "value present" and "absent" branches of resolution are exercised and the
    record carries nested mutable structures (lists, nested dataclasses) that a
    buggy projection could accidentally mutate.
    """
    return st.builds(
        CanonicalRecord,
        candidate_id=_opt_text,
        full_name=_opt_text,
        headline=_opt_text,
        years_experience=st.one_of(
            st.none(), st.floats(min_value=0.0, max_value=50.0)
        ),
        emails=st.lists(_text, max_size=4),
        phones=st.lists(_text, max_size=4),
        location=st.builds(
            Location, city=_opt_text, region=_opt_text, country=_opt_text
        ),
        links=st.builds(
            Links,
            linkedin=_opt_text,
            github=_opt_text,
            portfolio=_opt_text,
            other=st.lists(_text, max_size=3),
        ),
        skills=st.lists(_skill_strategy(), max_size=4),
        experience=st.lists(_experience_strategy(), max_size=3),
        education=st.lists(_education_strategy(), max_size=3),
        provenance=st.lists(_provenance_strategy(), max_size=4),
        overall_confidence=st.floats(min_value=0.0, max_value=1.0),
    )


@st.composite
def _config_dict(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a single valid Projection_Config dict.

    Selects a non-empty unique subset of the candidate paths, assigns each a unique
    output field name, and draws an optional normalization and an ``on_missing``
    behavior per field. The provenance / confidence toggles are drawn independently.
    """
    selected = draw(
        st.lists(
            st.sampled_from(_CANDIDATE_PATHS),
            min_size=1,
            max_size=len(_CANDIDATE_PATHS),
            unique=True,
        )
    )
    fields: list[dict[str, Any]] = []
    for i, path in enumerate(selected):
        entry: dict[str, Any] = {
            "name": f"out_{i}",
            "from": path,
            "on_missing": draw(st.sampled_from(_ON_MISSING_CHOICES)),
        }
        normalize = draw(st.sampled_from(_NORMALIZE_CHOICES))
        if normalize is not None:
            entry["normalize"] = normalize
        fields.append(entry)

    return {
        "fields": fields,
        "include_provenance": draw(st.booleans()),
        "include_confidence": draw(st.booleans()),
    }


@settings(deadline=None)
@given(
    record=_record_strategy(),
    config_dicts=st.lists(_config_dict(), min_size=1, max_size=4),
)
def test_projection_does_not_mutate_canonical_record(
    record: CanonicalRecord, config_dicts: list[dict[str, Any]]
) -> None:
    """Projecting one or more configs never mutates the shared canonical record.

    Validates Requirements 8.2, 15.1, 15.2, 15.3.
    """
    configs = [ProjectionConfig.from_dict(d) for d in config_dicts]

    # Snapshot the record's full state before any projection occurs.
    pristine = copy.deepcopy(record)

    # Project every config sequentially against the SAME record instance.
    for config in configs:
        project(record, config)
        # Req 8.2, 15.1, 15.3: after each projection the record is unchanged.
        assert record == pristine

    # Req 8.2, 15.1, 15.3: after all projections the record is still deep-equal to
    # its pre-projection snapshot (no mutation accumulated across configs).
    assert record == pristine

    # Req 15.2: each config produces its Projected_Profile from the same unchanged
    # canonical record. Projecting a config against the shared record (which has
    # already driven the preceding configs) yields the identical profile/errors as
    # projecting the same config against a fresh deep copy of the original record.
    for config in configs:
        fresh = copy.deepcopy(pristine)
        profile_shared, errors_shared = project(record, config)
        profile_fresh, errors_fresh = project(fresh, config)

        assert profile_shared == profile_fresh
        assert errors_shared == errors_fresh
        # The shared record remains untouched by these comparison projections too.
        assert record == pristine

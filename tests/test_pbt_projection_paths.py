"""Property-based test for projection path resolution.

Feature: candidate-data-transformer, Property 14

Property 14: Projection path resolution is correct.

    For any Canonical_Record and any valid Projection_Config, the Projected_Profile
    contains exactly the selected output field names (plus the optional
    provenance / overall_confidence keys per the include toggles), and each output
    value equals the value resolved from its ``from`` canonical path -- supporting
    nested paths (``location.city``), indexed elements (``phones[0]``), and array
    projections (``skills[].name``) -- with any declared per-field normalization
    applied.

**Validates: Requirements 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.10, 8.11, 8.12**

The oracle independently resolves each ``from`` path with
:func:`resolve_path`, serializes the result with :func:`to_dict`, and applies the
declared normalization, then compares against the engine's projected value. All
fields use ``on_missing="null"`` so an absent canonical field deterministically
maps to ``None`` (keeping the field present), which lets the test assert the exact
set of output keys without omit/error complications.
"""

from __future__ import annotations

import string
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.engine.path_resolver import MISSING, resolve_path
from candidate_transformer.engine.projection import ProjectionConfig, project
from candidate_transformer.models.canonical import (
    CanonicalRecord,
    Links,
    Location,
    Skill,
)
from candidate_transformer.models.serialization import to_dict

# Canonical ``from`` paths exercising every supported path shape: plain scalar,
# nested (``location.city``), indexed (``phones[0]``), nested scalar under links,
# and array projection (``skills[].name``).
_CANDIDATE_PATHS = [
    "full_name",
    "headline",
    "emails[0]",
    "phones[0]",
    "location.city",
    "location.region",
    "location.country",
    "links.linkedin",
    "links.github",
    "links.portfolio",
    "skills[].name",
]

# Per-field normalization transforms accepted by the projection engine (Req 8.10),
# plus ``None`` for "no normalization".
_NORMALIZE_CHOICES = [None, "lowercase", "uppercase"]

# A modest text alphabet keeps generated values readable while still exercising
# mixed case (so lowercase/uppercase normalization is observable).
_text = st.text(
    alphabet=string.ascii_letters + string.digits + " .-_@", min_size=0, max_size=12
)
_opt_text = st.one_of(st.none(), _text)


def _skill_strategy() -> st.SearchStrategy[Skill]:
    """A Skill with a possibly-null name; confidence/sources are irrelevant here."""
    return st.builds(Skill, name=_opt_text, confidence=st.just(0.0), sources=st.just([]))


def _record_strategy() -> st.SearchStrategy[CanonicalRecord]:
    """Generate canonical records with varied populated / absent fields.

    Covers every field referenced by ``_CANDIDATE_PATHS``: scalar fields may be
    ``None`` (absent) or populated, list fields may be empty or populated, and
    skills carry possibly-null names -- so both the "value present" and the
    "absent -> null" branches of resolution are exercised.
    """
    return st.builds(
        CanonicalRecord,
        full_name=_opt_text,
        headline=_opt_text,
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
    )


@st.composite
def _config_dict(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a valid Projection_Config dict.

    Selects a non-empty unique subset of the candidate paths, assigns each a unique
    renamed output field name (so the set of output keys is well defined and never
    collides with the reserved ``provenance`` / ``overall_confidence`` keys), draws
    an optional per-field normalization, and pins ``on_missing="null"``. The
    provenance / confidence toggles are drawn independently.
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
        normalize = draw(st.sampled_from(_NORMALIZE_CHOICES))
        entry: dict[str, Any] = {
            "name": f"out_{i}",
            "from": path,
            "on_missing": "null",
        }
        if normalize is not None:
            entry["normalize"] = normalize
        fields.append(entry)

    return {
        "fields": fields,
        "include_provenance": draw(st.booleans()),
        "include_confidence": draw(st.booleans()),
    }


def _apply_normalize(value: Any, transform: str | None) -> Any:
    """Independent oracle for the engine's per-field normalization (Req 8.10).

    Strings are transformed directly; list elements are transformed element-wise
    (non-strings unchanged); other scalars pass through.
    """
    if transform is None:
        return value
    func = str.lower if transform == "lowercase" else str.upper
    if isinstance(value, str):
        return func(value)
    if isinstance(value, list):
        return [func(item) if isinstance(item, str) else item for item in value]
    return value


@settings(deadline=None)
@given(record=_record_strategy(), config_dict=_config_dict())
def test_projection_path_resolution_is_correct(
    record: CanonicalRecord, config_dict: dict[str, Any]
) -> None:
    """Projected profile keys and values match independent path resolution."""
    config = ProjectionConfig.from_dict(config_dict)

    profile, errors = project(record, config)

    # With on_missing="null" and valid paths, projection reports no errors: absent
    # fields become null rather than omit/error, and the chosen paths are valid.
    assert errors == []

    # Req 8.1, 8.3, 8.11, 8.12: the output contains exactly the selected output
    # field names, plus provenance / overall_confidence only when their toggle is on.
    expected_keys = {spec.name for spec in config.fields}
    if config.include_provenance:
        expected_keys.add("provenance")
    if config.include_confidence:
        expected_keys.add("overall_confidence")
    assert set(profile.keys()) == expected_keys

    # Req 8.4, 8.5, 8.6, 8.7, 8.8, 8.10: each output value equals the value resolved
    # from its `from` path (nested / indexed / array projection), serialized the
    # same way the engine serializes it, with the declared normalization applied.
    for spec in config.fields:
        resolved = resolve_path(record, spec.from_)
        if resolved is MISSING:
            expected = None  # on_missing="null" emits an explicit null (Req 8.13).
        else:
            expected = _apply_normalize(to_dict(resolved), spec.normalize)
        assert profile[spec.name] == expected

    # Req 8.11 / 8.12: when included, the toggled keys carry the record's values.
    if config.include_provenance:
        assert profile["provenance"] == to_dict(record.provenance)
    if config.include_confidence:
        assert profile["overall_confidence"] == record.overall_confidence

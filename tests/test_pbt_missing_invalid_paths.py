"""Property-based test for missing-value and invalid-path handling.

Feature: candidate-data-transformer, Property 15

Property 15: Missing-value and invalid-path handling.

    For any Projection_Config field referencing an absent canonical field, the
    engine applies the configured ``on_missing`` behavior -- ``null`` emits null,
    ``omit`` excludes the field, ``error`` reports a projection error naming the
    field (and excludes the field) -- and any invalid path (an out-of-range array
    index such as ``phones[50]`` or a non-existent subfield such as ``skills[].abc``)
    produces a projection error naming the invalid path. In all cases the engine
    returns ``(profile, errors)`` without raising.

**Validates: Requirements 8.9, 8.13, 8.14, 8.15, 8.16**

Two complementary properties are exercised:

* ``test_on_missing_behavior_for_absent_fields`` builds a canonical record whose
  selected ``from`` paths all resolve to an absent field (scalar ``None`` or an
  empty list indexed at ``[0]``), assigns each a random ``on_missing`` behavior,
  and asserts the null / omit / error outcome per field.
* ``test_invalid_paths_report_projection_error`` builds a record with populated
  ``phones`` / ``skills`` lists, then references them with an out-of-range index
  or a non-existent subfield, and asserts each yields a ``stage="project"`` error
  whose message names the invalid path.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.engine.path_resolver import MISSING, resolve_path
from candidate_transformer.engine.projection import ProjectionConfig, project
from candidate_transformer.models.canonical import CanonicalRecord, Skill

# The three configured missing-value behaviors (Req 8.13, 8.14, 8.15).
_ON_MISSING = ["null", "omit", "error"]

# Canonical ``from`` paths that resolve to an *absent* field on an empty record:
# scalar fields default to ``None`` and indexed access into an empty list yields
# the MISSING sentinel (so on_missing applies). Array projections are deliberately
# excluded here because they yield an empty list (a value), not an absent field.
_ABSENT_PATHS = [
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
]

# Subfield tokens that are not attributes of :class:`Skill` (whose fields are
# ``name`` / ``confidence`` / ``sources``), so ``skills[].<token>`` is invalid.
_NONEXISTENT_SUBFIELDS = ["abc", "xyz", "nope", "missing_sub", "qty"]


@st.composite
def _missing_config(draw: st.DrawFn) -> tuple[dict[str, Any], dict[str, str]]:
    """Generate a config of absent-field references with random on_missing behavior.

    Selects a unique non-empty subset of the absent paths, assigns each a unique
    output name and a drawn ``on_missing`` value, and returns both the config dict
    and a ``{output_name: on_missing}`` map the test asserts against.
    """
    selected = draw(
        st.lists(
            st.sampled_from(_ABSENT_PATHS),
            min_size=1,
            max_size=len(_ABSENT_PATHS),
            unique=True,
        )
    )
    fields: list[dict[str, Any]] = []
    behaviors: dict[str, str] = {}
    for i, path in enumerate(selected):
        on_missing = draw(st.sampled_from(_ON_MISSING))
        name = f"out_{i}"
        fields.append(
            {
                "name": name,
                "from": path,
                "on_missing": on_missing,
                "required": on_missing == "error",
            }
        )
        behaviors[name] = on_missing

    config_dict = {
        "fields": fields,
        "include_provenance": False,
        "include_confidence": False,
    }
    return config_dict, behaviors


@settings(deadline=None)
@given(data=_missing_config())
def test_on_missing_behavior_for_absent_fields(
    data: tuple[dict[str, Any], dict[str, str]],
) -> None:
    """Absent fields honor their configured null / omit / error behavior."""
    config_dict, behaviors = data
    record = CanonicalRecord()  # every scalar None, every list empty -> all absent
    config = ProjectionConfig.from_dict(config_dict)

    # Sanity: each referenced path really does resolve to an absent field.
    for spec in config.fields:
        assert resolve_path(record, spec.from_) is MISSING

    # The engine must never raise -- it returns (profile, errors).
    profile, errors = project(record, config)

    for name, on_missing in behaviors.items():
        if on_missing == "null":
            # Req 8.13: an explicit null is emitted and the field stays present.
            assert name in profile
            assert profile[name] is None
        elif on_missing == "omit":
            # Req 8.14: the field is excluded from the output entirely.
            assert name not in profile
        else:  # "error"
            # Req 8.15: the field is excluded and a projection error names it.
            assert name not in profile
            assert any(e.stage == "project" and name in e.error for e in errors)

    # Only fields configured with on_missing="error" produce errors, and every
    # reported error is tagged to the projection stage.
    expected_error_names = {n for n, b in behaviors.items() if b == "error"}
    assert len(errors) == len(expected_error_names)
    assert all(e.stage == "project" for e in errors)


@st.composite
def _invalid_path_config(
    draw: st.DrawFn,
) -> tuple[CanonicalRecord, dict[str, Any], list[tuple[str, str]]]:
    """Generate a populated record plus fields referencing invalid paths.

    ``phones`` and ``skills`` are populated so that an out-of-range index
    (``phones[idx]`` with ``idx >= len``) and a non-existent subfield
    (``skills[].<token>``) are genuinely invalid rather than merely absent.
    Returns the record, the config dict, and a list of ``(output_name, path)``
    pairs the test asserts an error is reported for.
    """
    n_phones = draw(st.integers(min_value=1, max_value=4))
    phones = [f"+1415555{1000 + i}" for i in range(n_phones)]
    n_skills = draw(st.integers(min_value=1, max_value=4))
    skills = [
        Skill(name=f"Skill{i}", confidence=0.0, sources=[]) for i in range(n_skills)
    ]
    record = CanonicalRecord(phones=phones, skills=skills)

    count = draw(st.integers(min_value=1, max_value=5))
    fields: list[dict[str, Any]] = []
    expected: list[tuple[str, str]] = []
    for i in range(count):
        kind = draw(st.sampled_from(["index", "subfield"]))
        if kind == "index":
            # Index at or beyond the list length -> out-of-range (Req 8.16).
            idx = draw(st.integers(min_value=n_phones, max_value=n_phones + 50))
            path = f"phones[{idx}]"
        else:
            sub = draw(st.sampled_from(_NONEXISTENT_SUBFIELDS))
            path = f"skills[].{sub}"
        name = f"out_{i}"
        # on_missing must not mask an invalid path: invalid paths always error
        # regardless of the configured on_missing behavior.
        fields.append(
            {"name": name, "from": path, "on_missing": draw(st.sampled_from(_ON_MISSING))}
        )
        expected.append((name, path))

    config_dict = {
        "fields": fields,
        "include_provenance": False,
        "include_confidence": False,
    }
    return record, config_dict, expected


@settings(deadline=None)
@given(data=_invalid_path_config())
def test_invalid_paths_report_projection_error(
    data: tuple[CanonicalRecord, dict[str, Any], list[tuple[str, str]]],
) -> None:
    """Out-of-range indexes and non-existent subfields report path-naming errors."""
    record, config_dict, expected = data
    config = ProjectionConfig.from_dict(config_dict)

    # The engine must never raise on an invalid path -- it returns (profile, errors).
    profile, errors = project(record, config)

    for name, path in expected:
        # Req 8.16: the field is excluded and an error names the invalid path.
        assert name not in profile
        assert any(e.stage == "project" and path in e.error for e in errors)

    assert all(e.stage == "project" for e in errors)

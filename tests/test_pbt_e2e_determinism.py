"""Property-based test for end-to-end pipeline determinism.

Feature: candidate-data-transformer, Property 1

Property 1: End-to-end determinism
    For any set of source contents and any Projection_Config, running the full
    pipeline twice -- including with the input sources supplied in a different
    order -- produces byte-identical Projected_Profile output, identical
    list-valued field ordering, and identical provenance ordering.

**Validates: Requirements 7.8, 12.1, 12.2, 12.3, 16.1**

Strategy
--------
The pipeline reads real artifacts through the adapter registry, so this test
drives it with a fixed, lightweight subset of the shipped sample fixtures
(``recruiter.csv``, ``ats.json``, ``notes.txt``). Hypothesis explores the input
space by:

* choosing a non-empty subset of those fixtures (``source contents`` axis),
* supplying that subset in an arbitrary permutation (``different order`` axis),
* and toggling the projection config (``any Projection_Config`` axis) -- the
  field set, the ``include_provenance`` / ``include_confidence`` switches.

For each draw we assert:

* **Determinism (same order):** running twice over the same refs in the same
  order yields byte-identical serialized profiles (Req 12.1, 16.1).
* **Order independence:** running over a permutation of the refs yields the same
  set of profiles, matched by candidate id, with identical list-valued field
  ordering (Req 12.2) and identical provenance ordering (Req 12.3) per profile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine.transformer import TransformerEngine

# --------------------------------------------------------------------------- #
# Fixtures: a fixed, lightweight subset of the shipped sample artifacts.
# --------------------------------------------------------------------------- #
_SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# Lightweight, fast-parsing sources (no PDF/DOCX) that together describe
# overlapping candidates and so exercise identity matching + merge ordering.
_SAMPLE_FILES = ("recruiter.csv", "ats.json", "notes.txt")


def _ref(name: str) -> SourceRef:
    return SourceRef(location=str(_SAMPLES / name))


# --------------------------------------------------------------------------- #
# Projection configs. Both always project candidate_id as "id" so profiles from
# a reordered run can be matched back to their candidate deterministically.
# --------------------------------------------------------------------------- #
def _build_config(
    field_set: str, include_provenance: bool, include_confidence: bool
) -> dict[str, Any]:
    """Build a Projection_Config dict for one of the named field sets."""
    if field_set == "minimal":
        fields = [
            {"name": "id", "from": "candidate_id", "type": "string",
             "required": True, "on_missing": "error"},
            {"name": "emails", "from": "emails", "type": "array",
             "element_type": "string", "on_missing": "null"},
        ]
    else:  # "full": exercises scalars, indexed/array paths, list fields, nested paths.
        fields = [
            {"name": "id", "from": "candidate_id", "type": "string",
             "required": True, "on_missing": "error"},
            {"name": "name", "from": "full_name", "type": "string",
             "on_missing": "null"},
            {"name": "primary_email", "from": "emails[0]", "type": "string",
             "on_missing": "null"},
            {"name": "emails", "from": "emails", "type": "array",
             "element_type": "string", "on_missing": "null"},
            {"name": "phones", "from": "phones", "type": "array",
             "element_type": "string", "on_missing": "null"},
            {"name": "country", "from": "location.country", "type": "string",
             "on_missing": "omit"},
            {"name": "skills", "from": "skills[].name", "type": "array",
             "element_type": "string", "on_missing": "null"},
            {"name": "headline", "from": "headline", "type": "string",
             "on_missing": "null"},
            {"name": "linkedin", "from": "links.linkedin", "type": "string",
             "normalize": "lowercase", "on_missing": "omit"},
        ]
    return {
        "include_provenance": include_provenance,
        "include_confidence": include_confidence,
        "fields": fields,
    }


# --------------------------------------------------------------------------- #
# Hypothesis strategies.
# --------------------------------------------------------------------------- #
@st.composite
def _refs_and_permutation(draw: st.DrawFn) -> tuple[list[SourceRef], list[SourceRef]]:
    """Draw a non-empty subset of sample refs and an arbitrary permutation of it."""
    subset = draw(
        st.lists(st.sampled_from(_SAMPLE_FILES), min_size=1, max_size=len(_SAMPLE_FILES),
                 unique=True)
    )
    base = [_ref(name) for name in subset]
    permuted = draw(st.permutations(base))
    return base, list(permuted)


_configs = st.builds(
    _build_config,
    field_set=st.sampled_from(["minimal", "full"]),
    include_provenance=st.booleans(),
    include_confidence=st.booleans(),
)


# --------------------------------------------------------------------------- #
# Serialization helpers for byte-level and structural comparison.
# --------------------------------------------------------------------------- #
def _serialize(profiles: list[dict[str, Any]]) -> str:
    """Serialize the profiles list to canonical JSON text for byte comparison.

    Keys are kept in insertion order (``sort_keys=False``) so the assertion also
    catches any non-determinism in key/list/provenance ordering, not just values.
    """
    return json.dumps(profiles, sort_keys=False, ensure_ascii=False, default=str)


def _by_id(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index profiles by their projected candidate id ("id" field)."""
    indexed: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        indexed[str(profile.get("id"))] = profile
    return indexed


# --------------------------------------------------------------------------- #
# Property 1: End-to-end determinism.
# --------------------------------------------------------------------------- #
@settings(deadline=None)  # filesystem IO makes per-example timing variable
@given(refs_perm=_refs_and_permutation(), config=_configs)
def test_end_to_end_determinism(
    refs_perm: tuple[list[SourceRef], list[SourceRef]], config: dict[str, Any]
) -> None:
    """Pipeline output is byte-identical across re-runs and input reorderings."""
    base_refs, permuted_refs = refs_perm
    engine = TransformerEngine()

    # 1) Determinism: identical inputs + config -> byte-identical profiles (Req 12.1).
    first = engine.run(base_refs, config)
    second = engine.run(base_refs, config)
    assert _serialize(first.profiles) == _serialize(second.profiles)
    assert first.exit_code == second.exit_code

    # 2) Order independence: a permuted input order yields the same set of profiles
    #    (matched by candidate id), with identical list-valued field ordering
    #    (Req 12.2) and identical provenance ordering (Req 12.3) per profile.
    reordered = engine.run(permuted_refs, config)

    base_by_id = _by_id(first.profiles)
    reordered_by_id = _by_id(reordered.profiles)
    assert set(base_by_id) == set(reordered_by_id)
    for candidate_id, base_profile in base_by_id.items():
        # Deep structural equality covers every projected value, the ordering of
        # every list-valued field, and the ordering of provenance entries.
        assert reordered_by_id[candidate_id] == base_profile

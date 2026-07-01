"""Property-based tests for list-valued field deduplication.

Feature: candidate-data-transformer, Property 12

Property 12: List-valued fields are deduplicated.
    For any identity group, each list-valued field (emails, phones, skills,
    links.other) in the resulting Canonical_Record contains no duplicate values.

Validates: Requirements 5.6
"""

from __future__ import annotations

import random

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine.merge import (
    LIST_VALUED_FIELDS,
    ListContribution,
    combine_list_field,
    dedup_list_values,
)

# A small pool of values so that duplicates occur frequently across generated
# lists. Drawing from a constrained input space is what makes the dedup property
# meaningfully exercised.
_VALUE_POOL = [
    "jane@example.com",
    "jane.doe@example.com",
    "+1-555-0100",
    "+1-555-0199",
    "Python",
    "JavaScript",
    "Kubernetes",
    "https://example.com/jane",
    "https://github.com/jane",
]

# A pool of source types / ids used to build varied contributions.
_SOURCE_TYPES = ["recruiter_csv", "ats_json", "resume", "notes"]
_SOURCE_IDS = ["src-a", "src-b", "src-c", None]
_METHODS = ["regex", "alias", "exact", None]

values_strategy = st.lists(st.sampled_from(_VALUE_POOL), min_size=0, max_size=12)


def _contribution_strategy() -> st.SearchStrategy[ListContribution]:
    return st.builds(
        ListContribution,
        value=st.sampled_from(_VALUE_POOL),
        source_type=st.sampled_from(_SOURCE_TYPES),
        source_id=st.sampled_from(_SOURCE_IDS),
        method=st.sampled_from(_METHODS),
        field_confidence=st.floats(min_value=0.0, max_value=1.0),
    )


contributions_strategy = st.lists(_contribution_strategy(), min_size=0, max_size=12)


@given(values=values_strategy)
def test_dedup_list_values_has_no_duplicates(values: list[str]) -> None:
    """dedup_list_values collapses duplicates: len(result) == len(set(result))."""
    result = dedup_list_values(values)
    assert len(result) == len(set(result))
    # Every input value survives (set equality) and nothing is invented.
    assert set(result) == set(values)


@given(values=values_strategy, seed=st.integers(min_value=0, max_value=10_000))
def test_dedup_list_values_is_order_independent(values: list[str], seed: int) -> None:
    """The deduped result is deterministic and independent of input ordering."""
    baseline = dedup_list_values(values)

    # Calling again on the same input yields an identical result (determinism).
    assert dedup_list_values(values) == baseline

    # Shuffling the input yields the same sorted, deduped result.
    shuffled = list(values)
    random.Random(seed).shuffle(shuffled)
    assert dedup_list_values(shuffled) == baseline

    # The result is sorted in the deterministic canonical order.
    assert baseline == sorted(set(values))


@given(field_name=st.sampled_from(LIST_VALUED_FIELDS), contributions=contributions_strategy)
def test_combine_list_field_values_have_no_duplicates(
    field_name: str, contributions: list[ListContribution]
) -> None:
    """combine_list_field produces a duplicate-free value list for any field."""
    result = combine_list_field(field_name, contributions)

    assert len(result.values) == len(set(result.values))
    # The values are exactly the distinct contributed values (none invented).
    assert set(result.values) == {c.value for c in contributions}


@given(
    field_name=st.sampled_from(LIST_VALUED_FIELDS),
    contributions=contributions_strategy,
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_combine_list_field_is_order_independent(
    field_name: str, contributions: list[ListContribution], seed: int
) -> None:
    """combine_list_field values are deterministic and order-independent."""
    baseline = combine_list_field(field_name, contributions)

    shuffled = list(contributions)
    random.Random(seed).shuffle(shuffled)
    reordered = combine_list_field(field_name, shuffled)

    assert reordered.values == baseline.values
    assert len(reordered.values) == len(set(reordered.values))

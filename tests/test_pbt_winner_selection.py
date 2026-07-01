"""Property-based test for winner selection (Task 8.3).

Feature: candidate-data-transformer, Property 11

Property 11: Winner selection follows the policy deterministically
    Validates: Requirements 5.2, 5.3, 5.4, 5.5

For any set of conflicting candidate values for a single-valued field, the
selected winner is the first value under the ordering (SourcePriority, then
Field_Confidence, then Normalization_Quality, then stable lexical order), and the
result is independent of the order in which candidate values are presented.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.adapters.base import SOURCE_PRIORITY, priority_of
from candidate_transformer.engine import (
    CandidateValue,
    select_winner,
    winner_sort_key,
)

# The known source types, drawn from the fixed SourcePriority table.
_source_types = st.sampled_from(list(SOURCE_PRIORITY))

# Field_Confidence / Normalization_Quality live in [0, 1].
_unit_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

# A small, deliberately collision-prone set of string values so ties on the
# earlier ranking keys are exercised and the lexical tie-break is reached.
_values = st.sampled_from(["alpha", "beta", "gamma", "Alpha", "1", "10", "2"])


def _candidate() -> st.SearchStrategy[CandidateValue]:
    return st.builds(
        CandidateValue,
        value=_values,
        source_type=_source_types,
        field_confidence=_unit_floats,
        normalization_quality=_unit_floats,
        source_id=st.none(),
        method=st.none(),
    )


# A non-empty list of candidate values for a single-valued field.
_candidate_lists = st.lists(_candidate(), min_size=1, max_size=8)


def _expected_winner(candidates: list[CandidateValue]) -> CandidateValue:
    """The winner computed independently of select_winner, by sorting on the key."""
    return sorted(candidates, key=winner_sort_key)[0]


@given(candidates=_candidate_lists)
def test_winner_is_first_under_the_policy_ordering(candidates):
    """select_winner returns the first candidate under winner_sort_key (Req 5.3)."""
    winner = select_winner(candidates)
    expected = _expected_winner(candidates)
    assert winner_sort_key(winner) == winner_sort_key(expected)


@given(candidates=_candidate_lists)
def test_winner_is_the_min_under_the_sort_key(candidates):
    """The winner is the minimum under winner_sort_key: no candidate ranks ahead of it (Req 5.3, 5.4, 5.5)."""
    winner = select_winner(candidates)
    winner_key = winner_sort_key(winner)
    for candidate in candidates:
        assert winner_key <= winner_sort_key(candidate)


@given(candidates=_candidate_lists, perm=st.randoms(use_true_random=False))
def test_winner_is_order_independent(candidates, perm):
    """select_winner(shuffled) ranks identically to select_winner(candidates) (Req 5.2)."""
    shuffled = list(candidates)
    perm.shuffle(shuffled)
    assert winner_sort_key(select_winner(shuffled)) == winner_sort_key(
        select_winner(candidates)
    )


@given(candidates=_candidate_lists)
def test_higher_sourcepriority_value_wins_when_present(candidates):
    """The winner's source type is the most authoritative among the candidates (Req 5.4).

    SourcePriority is the first key, so the winner can never come from a less
    authoritative source than any other candidate.
    """
    winner = select_winner(candidates)
    best_priority = min(priority_of(c.source_type) for c in candidates)
    assert priority_of(winner.source_type) == best_priority

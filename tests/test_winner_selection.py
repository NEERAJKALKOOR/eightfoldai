"""Example unit tests for the Winner_Selection_Policy comparator (task 8.1).

Covers the four ordered criteria of the policy (Req 5.2-5.5):
SourcePriority -> Field_Confidence -> Normalization_Quality -> stable lexical order,
plus order-independence of the selected winner.
"""

from __future__ import annotations

import random

from candidate_transformer.engine.merge import (
    CandidateValue,
    select_winner,
    winner_sort_key,
)


def _shuffled(items: list[CandidateValue], seed: int) -> list[CandidateValue]:
    """Return a shuffled copy of ``items`` using a fixed seed (deterministic test)."""
    out = list(items)
    random.Random(seed).shuffle(out)
    return out


def test_source_priority_dominance() -> None:
    """A more authoritative source wins even with lower confidence/quality (Req 5.4)."""
    csv = CandidateValue(
        value="csv-value",
        source_type="recruiter_csv",  # priority rank 0 (most authoritative)
        field_confidence=0.10,
        normalization_quality=0.10,
    )
    github = CandidateValue(
        value="github-value",
        source_type="github",  # priority rank 4
        field_confidence=0.99,
        normalization_quality=0.99,
    )
    assert select_winner([csv, github]) is csv


def test_field_confidence_tiebreak() -> None:
    """Within the same source type, higher Field_Confidence wins (Req 5.3)."""
    low = CandidateValue(
        value="low",
        source_type="ats_json",
        field_confidence=0.40,
        normalization_quality=0.90,
    )
    high = CandidateValue(
        value="high",
        source_type="ats_json",
        field_confidence=0.80,
        normalization_quality=0.90,
    )
    assert select_winner([low, high]) is high


def test_normalization_quality_tiebreak() -> None:
    """Same source + confidence: higher Normalization_Quality wins (Req 5.3)."""
    coarse = CandidateValue(
        value="coarse",
        source_type="resume",
        field_confidence=0.70,
        normalization_quality=0.60,
    )
    clean = CandidateValue(
        value="clean",
        source_type="resume",
        field_confidence=0.70,
        normalization_quality=0.95,
    )
    assert select_winner([coarse, clean]) is clean


def test_lexical_final_tiebreak() -> None:
    """All else equal: stable lexical order of str(value) breaks the tie (Req 5.5)."""
    alpha = CandidateValue(
        value="alpha",
        source_type="linkedin",
        field_confidence=0.50,
        normalization_quality=0.50,
    )
    beta = CandidateValue(
        value="beta",
        source_type="linkedin",
        field_confidence=0.50,
        normalization_quality=0.50,
    )
    assert select_winner([alpha, beta]) is alpha
    # str() form is what is compared, so numeric values order lexically too.
    one = CandidateValue(value=10, source_type="linkedin")
    two = CandidateValue(value=9, source_type="linkedin")
    # "10" < "9" lexically, so 10 wins.
    assert select_winner([one, two]) is one


def test_order_independence_of_winner() -> None:
    """The winner is the same regardless of input ordering (Req 5.2)."""
    candidates = [
        CandidateValue("a", "github", 0.9, 0.9),
        CandidateValue("b", "recruiter_csv", 0.1, 0.1),
        CandidateValue("c", "ats_json", 0.5, 0.5),
        CandidateValue("d", "resume", 0.7, 0.2),
        CandidateValue("e", "recruiter_notes", 1.0, 1.0),
    ]
    expected = select_winner(candidates)
    assert expected is not None
    assert expected.source_type == "recruiter_csv"  # highest priority dominates

    for seed in range(20):
        assert select_winner(_shuffled(candidates, seed)) == expected


def test_full_ordering_applies_criteria_in_sequence() -> None:
    """Sorting by the key yields the documented priority->conf->quality->lexical order."""
    items = [
        CandidateValue("z", "ats_json", 0.5, 0.5),
        CandidateValue("a", "ats_json", 0.5, 0.5),  # ties z except lexical -> wins tie
        CandidateValue("x", "ats_json", 0.5, 0.9),  # higher quality beats the above
        CandidateValue("x", "ats_json", 0.9, 0.1),  # higher confidence beats quality
        CandidateValue("x", "recruiter_csv", 0.0, 0.0),  # top priority beats all
    ]
    ordered = sorted(items, key=winner_sort_key)
    assert ordered[0].source_type == "recruiter_csv"
    assert ordered[1] == CandidateValue("x", "ats_json", 0.9, 0.1)
    assert ordered[2] == CandidateValue("x", "ats_json", 0.5, 0.9)


def test_empty_candidates_returns_none() -> None:
    """Selecting from no candidates yields None."""
    assert select_winner([]) is None

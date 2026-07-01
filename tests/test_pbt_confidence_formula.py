"""Property-based test for the confidence model (Task 9.2).

Feature: candidate-data-transformer, Property 4

Property 4: Confidence formula correctness
    Validates: Requirements 7.2, 7.4

For any ``Source_Reliability``, ``Agreement_Score``, and ``Normalization_Quality``
each in ``[0, 1]``, the computed ``Field_Confidence`` equals
``clamp(0.5 * reliability + 0.3 * agreement + 0.2 * quality, 0, 1)`` (Req 7.2).

The ``Agreement_Score`` equals ``(sources supplying the winning value) /
(sources containing the field)``, or ``0.0`` when no source contains the field
(Req 7.4).
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine import agreement_score, field_confidence

# Floats constrained to the documented input space [0, 1].
unit_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@given(reliability=unit_floats, agreement=unit_floats, quality=unit_floats)
def test_field_confidence_matches_clamped_weighted_sum(reliability, agreement, quality):
    """Field_Confidence == clamp(0.5*r + 0.3*a + 0.2*q, 0, 1) (Req 7.2)."""
    expected_raw = 0.5 * reliability + 0.3 * agreement + 0.2 * quality
    expected = min(1.0, max(0.0, expected_raw))

    result = field_confidence(reliability, agreement, quality)

    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)
    # With inputs in [0, 1] the weighted sum already lies in [0, 1].
    assert 0.0 <= result <= 1.0


@given(
    containing=st.integers(min_value=1, max_value=10_000),
    data=st.data(),
)
def test_agreement_score_is_supplying_over_containing(containing, data):
    """Agreement_Score == supplying / containing for 0 <= supplying <= containing (Req 7.4)."""
    supplying = data.draw(st.integers(min_value=0, max_value=containing))

    result = agreement_score(supplying, containing)
    expected = supplying / containing

    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)
    assert 0.0 <= result <= 1.0


@given(supplying=st.integers(min_value=0, max_value=10_000))
def test_agreement_score_is_zero_when_no_source_contains_field(supplying):
    """Agreement_Score == 0.0 when no source contains the field (Req 7.4)."""
    assert agreement_score(supplying, 0) == 0.0

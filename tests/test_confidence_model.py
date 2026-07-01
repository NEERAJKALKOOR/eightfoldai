"""Unit tests for the confidence model (Req 7).

Covers the Confidence_Formula and clamping (Req 7.2), Source_Reliability inputs
(Req 7.3), Agreement_Score including the zero-denominator edge case (Req 7.4),
agreement monotonicity (Req 7.5), Overall_Confidence as the mean of non-null field
confidences (Req 7.6), null-field zero confidence (Req 7.7), and determinism
(Req 7.8).
"""

from __future__ import annotations

import pytest

from candidate_transformer.adapters.base import reliability_of
from candidate_transformer.engine.confidence import (
    AGREEMENT_WEIGHT,
    QUALITY_WEIGHT,
    RELIABILITY_WEIGHT,
    agreement_score,
    clamp_unit,
    field_confidence,
    null_field_confidence,
    overall_confidence,
)


class TestFieldConfidenceFormula:
    """Field_Confidence = clamp(0.5*rel + 0.3*agree + 0.2*qual, 0, 1) (Req 7.2)."""

    def test_weights_match_design(self) -> None:
        assert (RELIABILITY_WEIGHT, AGREEMENT_WEIGHT, QUALITY_WEIGHT) == (0.5, 0.3, 0.2)

    @pytest.mark.parametrize(
        "rel,agree,qual,expected",
        [
            (1.0, 1.0, 1.0, 1.0),
            (0.0, 0.0, 0.0, 0.0),
            (0.95, 1.0, 1.0, 0.5 * 0.95 + 0.3 + 0.2),  # 0.975
            (0.80, 0.5, 0.6, 0.5 * 0.80 + 0.3 * 0.5 + 0.2 * 0.6),  # 0.67
            (0.60, 0.0, 1.0, 0.5 * 0.60 + 0.2),  # 0.5
        ],
    )
    def test_formula_values(
        self, rel: float, agree: float, qual: float, expected: float
    ) -> None:
        assert field_confidence(rel, agree, qual) == pytest.approx(expected)

    def test_uses_real_reliability_weights(self) -> None:
        # Single source, perfect agreement and quality => 0.5*rel + 0.3 + 0.2.
        rel = reliability_of("recruiter_csv")  # 0.95
        assert field_confidence(rel, 1.0, 1.0) == pytest.approx(0.975)

    def test_result_always_in_unit_interval(self) -> None:
        for rel in (0.0, 0.5, 1.0):
            for agree in (0.0, 0.5, 1.0):
                for qual in (0.0, 0.5, 1.0):
                    c = field_confidence(rel, agree, qual)
                    assert 0.0 <= c <= 1.0


class TestClamping:
    """Out-of-range inputs are clamped to [0, 1] (Req 7.2)."""

    @pytest.mark.parametrize(
        "value,expected",
        [(-1.0, 0.0), (-0.001, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (1.5, 1.0)],
    )
    def test_clamp_unit(self, value: float, expected: float) -> None:
        assert clamp_unit(value) == expected

    def test_formula_clamps_high(self) -> None:
        # Inputs above 1 would overflow the raw sum; the clamp caps at 1.0.
        assert field_confidence(2.0, 2.0, 2.0) == 1.0

    def test_formula_clamps_low(self) -> None:
        assert field_confidence(-1.0, -1.0, -1.0) == 0.0


class TestAgreementScore:
    """Agreement_Score = supplying / containing, 0.0 when denominator is 0 (Req 7.4)."""

    @pytest.mark.parametrize(
        "supplying,containing,expected",
        [
            (1, 1, 1.0),
            (1, 2, 0.5),
            (2, 4, 0.5),
            (3, 3, 1.0),
            (0, 5, 0.0),
        ],
    )
    def test_ratio(self, supplying: int, containing: int, expected: float) -> None:
        assert agreement_score(supplying, containing) == pytest.approx(expected)

    def test_zero_denominator_is_zero(self) -> None:
        # No source contains the field -> 0.0 (avoids divide-by-zero).
        assert agreement_score(0, 0) == 0.0

    def test_negative_denominator_is_zero(self) -> None:
        assert agreement_score(0, -1) == 0.0

    def test_result_in_unit_interval(self) -> None:
        assert 0.0 <= agreement_score(1, 3) <= 1.0


class TestAgreementMonotonicity:
    """More agreeing sources never lowers Field_Confidence (Req 7.5)."""

    def test_more_agreement_raises_or_equal_confidence(self) -> None:
        rel, qual = 0.85, 1.0
        # One source supplies the value out of N containing the field.
        single = field_confidence(rel, agreement_score(1, 3), qual)
        # All three sources agree on the winning value.
        agreeing = field_confidence(rel, agreement_score(3, 3), qual)
        assert agreeing >= single


class TestNullFieldConfidence:
    """A null field gets Field_Confidence of exactly 0.0 (Req 7.7)."""

    def test_null_field_is_zero(self) -> None:
        assert null_field_confidence() == 0.0


class TestOverallConfidence:
    """Overall_Confidence = mean of non-null field confidences, 0.0 if all null."""

    def test_mean_of_values(self) -> None:
        assert overall_confidence([0.2, 0.4, 0.6]) == pytest.approx(0.4)

    def test_all_null_is_zero(self) -> None:
        assert overall_confidence([]) == 0.0

    def test_present_zero_confidences_are_included(self) -> None:
        # Present fields are signalled by a non-None entry and are always included,
        # even when their score is exactly 0.0; the mean of three present zeros is 0.0.
        assert overall_confidence([0.0, 0.0, 0.0]) == 0.0

    def test_none_entries_are_skipped(self) -> None:
        # None marks a null field; mean is over the remaining present values.
        assert overall_confidence([None, 0.6, None, 0.8]) == pytest.approx(0.7)

    def test_null_fields_use_none_not_zero(self) -> None:
        # A null field is passed as None and excluded; mean of the two present values.
        assert overall_confidence([0.9, None, 0.5]) == pytest.approx(0.7)

    def test_present_zero_is_not_treated_as_null(self) -> None:
        # A present-but-zero field is included (not silently dropped), so it pulls
        # the mean down rather than inflating it: mean of 0.9, 0.0, 0.5.
        assert overall_confidence([0.9, 0.0, 0.5]) == pytest.approx(1.4 / 3)

    def test_result_in_unit_interval(self) -> None:
        assert 0.0 <= overall_confidence([0.1, 0.9, 1.0]) <= 1.0


class TestDeterminism:
    """Identical inputs produce identical outputs (Req 7.8)."""

    def test_field_confidence_repeatable(self) -> None:
        assert field_confidence(0.8, 0.5, 0.6) == field_confidence(0.8, 0.5, 0.6)

    def test_overall_confidence_repeatable(self) -> None:
        vals = [0.3, 0.7, None, 0.5]
        assert overall_confidence(vals) == overall_confidence(vals)

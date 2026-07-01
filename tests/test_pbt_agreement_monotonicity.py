"""Property-based test for agreement monotonicity (Task 9.4).

Feature: candidate-data-transformer, Property 6

**Property 6: Agreement monotonicity** -- For any field, the Field_Confidence
computed when N sources agree on the winning value is greater than or equal to the
Field_Confidence computed when only one source provides that value (all else equal).

**Validates: Requirements 7.5**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine import agreement_score, field_confidence

# Floats constrained to the closed unit interval [0, 1], the valid input space for
# Source_Reliability and Normalization_Quality.
unit_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@given(
    reliability=unit_floats,
    quality=unit_floats,
    data=st.data(),
)
def test_field_confidence_is_non_decreasing_in_agreeing_sources(
    reliability: float,
    quality: float,
    data: st.DataObject,
) -> None:
    """More agreeing sources never lowers Field_Confidence (all else equal).

    Fix the source reliability and normalization quality, then compare the
    Field_Confidence when a single source supplies the winning value against the
    Field_Confidence when ``n_agree`` (>= 1) sources agree on it, holding the number
    of sources containing the field constant. The latter must be >= the former.
    """
    containing = data.draw(st.integers(min_value=1, max_value=50))
    n_agree = data.draw(st.integers(min_value=1, max_value=containing))

    single = field_confidence(
        reliability,
        agreement_score(1, containing),
        quality,
    )
    many = field_confidence(
        reliability,
        agreement_score(n_agree, containing),
        quality,
    )

    assert many >= single

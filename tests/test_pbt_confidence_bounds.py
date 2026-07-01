"""Property-based tests for confidence bounds and null-honesty.

Feature: candidate-data-transformer, Property 5

Property 5: Confidence bounds and null-honesty of confidence
    For any assembled Canonical_Record, every Field_Confidence and the
    Overall_Confidence lie in [0,1], every Normalization_Quality lies in [0,1],
    and every field whose value is null has a Field_Confidence of exactly 0.0.

**Validates: Requirements 3.10, 7.1, 7.6, 7.7**

The property is exercised against the pure scoring primitives that produce those
values:

* :func:`field_confidence` -- the Confidence_Formula result is always in [0,1]
  even when its inputs stray outside [0,1] (Req 7.1).
* :func:`overall_confidence` -- the mean over any list of per-field confidences
  (with ``None`` entries standing in for null fields) is always in [0,1]
  (Req 7.6).
* :func:`null_field_confidence` -- a null-valued field scores exactly 0.0
  (Req 7.7).
* every normalizer -- the reported ``normalization_quality`` is always in [0,1]
  (Req 3.10), and a ``None`` value is always paired with a quality of exactly
  0.0 (null-honesty).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine import (
    field_confidence,
    null_field_confidence,
    overall_confidence,
)
from candidate_transformer.normalizers import (
    normalize_country,
    normalize_date,
    normalize_email,
    normalize_phone,
    normalize_skill,
)

# Inputs that may be in-range or stray outside [0,1] -- the formula must clamp
# regardless. ``allow_nan``/``allow_infinity`` are disabled because confidence
# inputs are always finite real weights.
_floats = st.floats(
    min_value=-1000.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# In-range unit-interval floats for inputs that are documented to be in [0,1].
_unit_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Field_Confidence is always bounded in [0, 1] (Req 7.1)
# ---------------------------------------------------------------------------
@given(reliability=_floats, agreement=_floats, quality=_floats)
def test_field_confidence_is_bounded(reliability, agreement, quality):
    """For any inputs (even out of range), Field_Confidence lies in [0, 1]."""
    result = field_confidence(reliability, agreement, quality)
    assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# Overall_Confidence is always bounded in [0, 1] (Req 7.6)
# ---------------------------------------------------------------------------
@given(
    confidences=st.lists(
        st.one_of(st.none(), _unit_floats),
        max_size=30,
    )
)
def test_overall_confidence_is_bounded(confidences):
    """For any list of per-field confidences (with None null entries), the
    Overall_Confidence lies in [0, 1]."""
    result = overall_confidence(confidences)
    assert 0.0 <= result <= 1.0


@given(
    confidences=st.lists(
        st.one_of(st.none(), _floats),
        max_size=30,
    )
)
def test_overall_confidence_bounded_even_with_unclamped_inputs(confidences):
    """Even if individual confidences stray outside [0, 1], the mean is clamped
    into [0, 1]."""
    result = overall_confidence(confidences)
    assert 0.0 <= result <= 1.0


def test_overall_confidence_all_null_is_zero():
    """A record whose fields are all null (all None) has Overall_Confidence 0.0."""
    assert overall_confidence([None, None, None]) == 0.0
    assert overall_confidence([]) == 0.0


# ---------------------------------------------------------------------------
# Null fields score exactly 0.0 (Req 7.7)
# ---------------------------------------------------------------------------
def test_null_field_confidence_is_exactly_zero():
    """A null-valued field has a Field_Confidence of exactly 0.0."""
    assert null_field_confidence() == 0.0


# ---------------------------------------------------------------------------
# Normalization_Quality is always in [0, 1] and null-honest (Req 3.10)
# ---------------------------------------------------------------------------
# A broad strategy of arbitrary inputs: text (incl. unicode/garbage), plus
# non-string types the normalizers must tolerate without raising.
_arbitrary_input = st.one_of(
    st.text(max_size=40),
    st.none(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
)

_normalizers = [
    normalize_phone,
    normalize_date,
    normalize_country,
    normalize_skill,
    normalize_email,
]


@given(raw=_arbitrary_input)
def test_normalizers_quality_bounded_and_null_honest(raw):
    """For any input, every normalizer returns a quality in [0, 1], and a null
    value is always paired with a quality of exactly 0.0 (null-honesty)."""
    for normalize in _normalizers:
        value, quality = normalize(raw)
        assert 0.0 <= quality <= 1.0, (normalize.__name__, raw, quality)
        if value is None:
            assert quality == 0.0, (normalize.__name__, raw, quality)

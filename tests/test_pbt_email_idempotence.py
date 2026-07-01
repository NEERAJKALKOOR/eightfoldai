"""Property-based test for email normalization idempotence.

Feature: candidate-data-transformer, Property 8

Property 8: Email normalization is idempotent (``normalize(normalize(e)) == normalize(e)``).

For any email string, normalization produces a trimmed, lowercased value, and
normalizing an already-normalized email returns the same value and quality.

The minimum of 100 iterations is provided by the project's default Hypothesis
``ci`` profile (registered in ``tests/conftest.py`` with ``max_examples=100``).

**Validates: Requirements 3.9**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.normalizers import normalize_email


# A strategy mixing arbitrary text with email-like strings so we exercise both
# the valid-syntax and cleaned-but-invalid branches of the normalizer.
_email_like = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    st.text(min_size=0, max_size=10),
    st.text(min_size=0, max_size=10),
    st.text(min_size=0, max_size=5),
)

_inputs = st.one_of(
    st.text(),
    _email_like,
    # Surround with assorted whitespace to exercise trimming explicitly.
    st.builds(lambda s: f"  \t{s}\n ", st.text()),
)


@given(raw=_inputs)
def test_email_normalization_is_idempotent(raw: str) -> None:
    """normalize(normalize(e)) == normalize(e) and value is trimmed+lowercased."""
    value, quality = normalize_email(raw)

    if value is None:
        # Canonical null result: empty/whitespace-only input yields (None, 0.0).
        assert quality == 0.0
        return

    # The normalized value must equal its own trimmed+lowercased form.
    assert value == raw.strip().lower()
    assert value == value.strip().lower()

    # Idempotence: re-normalizing the already-normalized value reproduces it
    # exactly, with the same quality score.
    revalue, requality = normalize_email(value)
    assert revalue == value
    assert requality == quality

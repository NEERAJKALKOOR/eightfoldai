"""Property-based test for normalization canonical-or-null (Task 3.4).

Feature: candidate-data-transformer, Property 7

Property 7: Normalization yields canonical format or null.
*For any* input string, each normalizer returns either ``None`` or a value in its
canonical format: phones match E.164, dates match ``YYYY-MM``, countries are valid
ISO-3166 alpha-2 codes, and skills are members of the Controlled_Skill_Vocabulary.
A non-convertible value always yields ``None`` and never an invented or
partially-formatted value.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from __future__ import annotations

import re

import phonenumbers
import pycountry
from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.normalizers import (
    Controlled_Skill_Vocabulary,
    normalize_country,
    normalize_date,
    normalize_phone,
    normalize_skill,
)

# ---------------------------------------------------------------------------
# Canonical-format oracles
# ---------------------------------------------------------------------------

# E.164: a leading "+", a non-zero leading digit, then up to 14 more digits.
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")

# YYYY-MM with a valid two-digit month (01-12).
_YYYY_MM_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Every valid ISO-3166 alpha-2 code in the pycountry dataset.
_VALID_ALPHA2 = frozenset(country.alpha_2 for country in pycountry.countries)

# The canonical skill names that a successful skill normalization may produce.
_CANONICAL_SKILL_NAMES = frozenset(Controlled_Skill_Vocabulary.keys())


# ---------------------------------------------------------------------------
# Input strategies
# ---------------------------------------------------------------------------
#
# A blend of fully-arbitrary text (to exercise the never-crash / honest-null
# contract on garbage) and structured-but-plausible inputs (to actually reach the
# success branches of each normalizer, so the canonical-format assertions get
# exercised rather than only the null path).

_arbitrary_text = st.text(max_size=40)

# Phone-ish: optional "+", digits, spaces, and common separators.
_phoneish = st.text(alphabet="+0123456789()-. ", max_size=20)

# Date-ish: digits, separators, and letters (to hit month-name parsing too).
_dateish = st.text(
    alphabet="0123456789/-. ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    max_size=20,
)

# Country-ish: a mix of arbitrary words plus real codes/names so exact and fuzzy
# branches are reachable.
_country_tokens = ["US", "us", "USA", "United States", "IN", "India", "GB",
                   "United Kingdom", "Germany", "DE", "Atlantis", "Xyz", ""]
_countryish = st.one_of(st.sampled_from(_country_tokens), _arbitrary_text)

# Skill-ish: canonical names, known aliases, and arbitrary tokens.
_skill_aliases = [a for aliases in Controlled_Skill_Vocabulary.values() for a in aliases]
_skill_tokens = list(Controlled_Skill_Vocabulary.keys()) + _skill_aliases
_skillish = st.one_of(st.sampled_from(_skill_tokens), _arbitrary_text)


# ---------------------------------------------------------------------------
# Property 7 — one test per normalizer
# ---------------------------------------------------------------------------


@given(st.one_of(_arbitrary_text, _phoneish))
def test_phone_is_e164_or_null(raw: str) -> None:
    """normalize_phone returns either None or a valid E.164 string."""
    value, quality = normalize_phone(raw, default_region="US")
    assert 0.0 <= quality <= 1.0
    if value is None:
        assert quality == 0.0
    else:
        # Canonical E.164 shape, and a genuinely valid number (no invented value).
        assert _E164_RE.match(value), f"not E.164: {value!r}"
        parsed = phonenumbers.parse(value, None)
        assert phonenumbers.is_valid_number(parsed)


@given(st.one_of(_arbitrary_text, _dateish))
def test_date_is_yyyy_mm_or_null(raw: str) -> None:
    """normalize_date returns either None or a canonical YYYY-MM string."""
    value, quality = normalize_date(raw)
    assert 0.0 <= quality <= 1.0
    if value is None:
        assert quality == 0.0
    else:
        assert _YYYY_MM_RE.match(value), f"not YYYY-MM: {value!r}"


@given(_countryish)
def test_country_is_iso_alpha2_or_null(raw: str) -> None:
    """normalize_country returns either None or a valid ISO-3166 alpha-2 code."""
    value, quality = normalize_country(raw)
    assert 0.0 <= quality <= 1.0
    if value is None:
        assert quality == 0.0
    else:
        assert value in _VALID_ALPHA2, f"not a valid alpha-2 code: {value!r}"


@given(_skillish)
def test_skill_is_canonical_or_null(raw: str) -> None:
    """normalize_skill returns either None or a Canonical_Skill_Name."""
    value, quality = normalize_skill(raw)
    assert 0.0 <= quality <= 1.0
    if value is None:
        assert quality == 0.0
    else:
        assert value in _CANONICAL_SKILL_NAMES, f"not in vocabulary: {value!r}"

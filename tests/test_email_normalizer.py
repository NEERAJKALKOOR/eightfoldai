"""Unit tests for the email normalizer (Task 3.3, Requirements 3.9, 3.10)."""

from __future__ import annotations

import pytest

from candidate_transformer.normalizers import normalize_email


def test_trims_and_lowercases_valid_email():
    value, quality = normalize_email("  Jane.Doe@example.com ")
    assert value == "jane.doe@example.com"
    assert quality == 1.0


def test_already_lowercased_valid_email():
    value, quality = normalize_email("JANE@EXAMPLE.COM")
    assert value == "jane@example.com"
    assert quality == 1.0


def test_invalid_email_is_cleaned_not_nulled():
    value, quality = normalize_email("not-an-email")
    assert value == "not-an-email"
    assert quality == 0.7


def test_empty_string_returns_none():
    assert normalize_email("") == (None, 0.0)


def test_whitespace_only_returns_none():
    assert normalize_email("   \t\n ") == (None, 0.0)


def test_none_returns_none():
    assert normalize_email(None) == (None, 0.0)


def test_non_string_returns_none():
    assert normalize_email(12345) == (None, 0.0)


@pytest.mark.parametrize(
    "raw",
    [
        "  Jane.Doe@example.com ",
        "JANE@EXAMPLE.COM",
        "not-an-email",
        "a@b@c.com",
        "user@nodot",
        "@example.com",
        "local@",
        "user@example.com",
        "x@y.co.uk",
    ],
)
def test_idempotence(raw):
    value, quality = normalize_email(raw)
    if value is not None:
        revalue, requality = normalize_email(value)
        assert revalue == value
        assert requality == quality


@pytest.mark.parametrize(
    "raw",
    [
        "user @example.com",  # space in local part
        "user@exa mple.com",  # space in domain
        "userexample.com",  # missing @
        "a@b@c.com",  # two @
        "user@nodot",  # domain has no dot
        "@example.com",  # empty local part
        "local@",  # empty domain
        "user@.com",  # empty domain label
        "user@example.",  # trailing dot label empty
    ],
)
def test_invalid_syntax_scores_low_but_returns_value(raw):
    value, quality = normalize_email(raw)
    assert value == raw.strip().lower()
    assert quality == 0.7


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a@b.co", 1.0),
        ("first.last+tag@sub.domain.org", 1.0),
        ("USER@EXAMPLE.COM", 1.0),
    ],
)
def test_valid_syntax_scores_full(raw, expected):
    value, quality = normalize_email(raw)
    assert quality == expected
    assert value == raw.strip().lower()

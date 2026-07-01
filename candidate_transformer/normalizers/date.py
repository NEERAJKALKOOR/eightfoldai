"""Date normalization to ``YYYY-MM`` (Req 3.3, 3.4).

``normalize_date`` converts a raw date string into the canonical ``YYYY-MM`` form
(e.g. ``2019-03``). It recognizes the common formats that appear in resumes and
recruiter data, parsed deterministically with explicit patterns (no wall-clock or
locale-dependent behavior):

  * ``"2019-03"`` / ``"2019/03"``                 (ISO-ish year-month)
  * ``"2019-03-15"`` / ``"2019/03/15"``           (full ISO date; day ignored)
  * ``"03/2019"`` / ``"03-2019"``                 (month-year)
  * ``"March 2019"`` / ``"Mar 2019"`` / ``"2019 March"``  (month name + year)
  * ``"2019"``                                    (year only)

Quality scoring (Req 3.10):
  * ``1.0`` — both month and year were present in the input.
  * ``0.6`` — only a year was present; month ``01`` is assumed as a fallback.

Null rule (Req 3.4): any value that cannot be parsed into at least a valid year —
including ``None``, empty/garbage strings, and out-of-range months — yields
``(None, 0.0)``. The function never raises.
"""

from __future__ import annotations

import re

from .common import NULL_RESULT, NormalizationResult

_QUALITY_MONTH_AND_YEAR = 1.0
_QUALITY_YEAR_ONLY = 0.6

# Plausible four-digit calendar years. Bounding the range keeps stray 4-digit
# numbers (and absurd years) from being accepted as dates.
_MIN_YEAR = 1900
_MAX_YEAR = 2099

# Month-name lookup (full and three-letter abbreviations), lowercased.
_MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Numeric patterns, tried in order.
_RE_YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?$")
_RE_MONTH_YEAR = re.compile(r"^(\d{1,2})[-/](\d{4})$")
_RE_YEAR_ONLY = re.compile(r"^(\d{4})$")
# Month name combined with a year, in either order.
_RE_NAME_YEAR = re.compile(r"^([A-Za-z]+)[\s,]+(\d{4})$")
_RE_YEAR_NAME = re.compile(r"^(\d{4})[\s,]+([A-Za-z]+)$")


def _valid_year(year: int) -> bool:
    return _MIN_YEAR <= year <= _MAX_YEAR


def _format(year: int, month: int, quality: float) -> NormalizationResult:
    if not _valid_year(year) or not (1 <= month <= 12):
        return NULL_RESULT
    return (f"{year:04d}-{month:02d}", quality)


def normalize_date(raw: object) -> NormalizationResult:
    """Normalize ``raw`` to ``YYYY-MM``, returning ``(value | None, quality)``.

    Deterministic and total: never raises. Returns ``(None, 0.0)`` for any input
    that cannot be parsed into a valid year (Req 3.4).
    """
    if not isinstance(raw, str):
        return NULL_RESULT

    text = raw.strip()
    if not text:
        return NULL_RESULT

    # 1. Year-month (and full ISO date with an ignored day): full quality.
    m = _RE_YEAR_MONTH.match(text)
    if m:
        return _format(int(m.group(1)), int(m.group(2)), _QUALITY_MONTH_AND_YEAR)

    # 2. Month-year (e.g. "03/2019"): full quality.
    m = _RE_MONTH_YEAR.match(text)
    if m:
        return _format(int(m.group(2)), int(m.group(1)), _QUALITY_MONTH_AND_YEAR)

    # 3. Month name + year (either order): full quality.
    m = _RE_NAME_YEAR.match(text)
    if m:
        month = _MONTH_NAMES.get(m.group(1).lower())
        if month is not None:
            return _format(int(m.group(2)), month, _QUALITY_MONTH_AND_YEAR)
        # An unrecognized leading word that is not a month name is not a date.
        return NULL_RESULT

    m = _RE_YEAR_NAME.match(text)
    if m:
        month = _MONTH_NAMES.get(m.group(2).lower())
        if month is not None:
            return _format(int(m.group(1)), month, _QUALITY_MONTH_AND_YEAR)
        return NULL_RESULT

    # 4. Year only: assume month 01, reduced quality.
    m = _RE_YEAR_ONLY.match(text)
    if m:
        return _format(int(m.group(1)), 1, _QUALITY_YEAR_ONLY)

    return NULL_RESULT

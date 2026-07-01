"""Country normalization to ISO-3166 alpha-2 (Req 3.5, 3.6).

``normalize_country`` converts a raw country value into its canonical two-letter
ISO-3166 alpha-2 code (e.g. ``US``, ``IN``, ``GB``) using the ``pycountry``
dataset.

Resolution strategy (deterministic, tried in order):
  1. **Exact match** on an ISO code (alpha-2 or alpha-3) or on an official country
     name (``name``, ``official_name``, or ``common_name``), case-insensitive.
     Examples: ``"US"``, ``"USA"``, ``"United States"`` all resolve to ``US``.
  2. **Fuzzy / alias match** via ``pycountry``'s fuzzy search for inputs that are
     not an exact code or name but still clearly identify a country
     (e.g. minor misspellings or partial names).

Quality scoring (Req 3.10):
  * ``1.0`` — exact code or exact name match (step 1).
  * ``0.7`` — fuzzy / alias match (step 2).

Null rule (Req 3.6): any value that cannot be resolved to a country — including
``None``, empty/garbage strings, and unknown names like ``"Atlantis"`` — yields
``(None, 0.0)``. The function never raises.

Note on the "exact" classification: an ISO alpha-3 code such as ``"USA"`` and the
official name ``"United States"`` are treated as *exact* matches (quality ``1.0``)
because they are unambiguous, canonical identifiers in the ISO dataset, per the
design's normalization table ("1.0 exact code/name match"). Only genuinely
approximate inputs fall through to the fuzzy path.
"""

from __future__ import annotations

import pycountry

from .common import NULL_RESULT, NormalizationResult

_QUALITY_EXACT = 1.0
_QUALITY_FUZZY = 0.7


def _exact_match(text: str) -> str | None:
    """Return the alpha-2 code for an exact code/name match, else ``None``."""
    upper = text.upper()

    # ISO codes are 2 or 3 letters; only attempt code lookups for those lengths.
    if len(upper) == 2:
        country = pycountry.countries.get(alpha_2=upper)
        if country is not None:
            return country.alpha_2
    if len(upper) == 3:
        country = pycountry.countries.get(alpha_3=upper)
        if country is not None:
            return country.alpha_2

    # Exact (case-insensitive) name matches against the dataset's name fields.
    lowered = text.lower()
    for country in pycountry.countries:
        names = [
            getattr(country, "name", None),
            getattr(country, "official_name", None),
            getattr(country, "common_name", None),
        ]
        for name in names:
            if name is not None and name.lower() == lowered:
                return country.alpha_2

    return None


def _fuzzy_match(text: str) -> str | None:
    """Return the alpha-2 code for a fuzzy match, else ``None``."""
    try:
        matches = pycountry.countries.search_fuzzy(text)
    except LookupError:
        return None
    if not matches:
        return None
    return matches[0].alpha_2


def normalize_country(raw: object) -> NormalizationResult:
    """Normalize ``raw`` to an ISO-3166 alpha-2 code, returning ``(code | None, quality)``.

    Deterministic and total: never raises. Returns ``(None, 0.0)`` for any input
    that cannot be resolved to a country (Req 3.6).
    """
    if not isinstance(raw, str):
        return NULL_RESULT

    text = raw.strip()
    if not text:
        return NULL_RESULT

    code = _exact_match(text)
    if code is not None:
        return (code, _QUALITY_EXACT)

    code = _fuzzy_match(text)
    if code is not None:
        return (code, _QUALITY_FUZZY)

    return NULL_RESULT

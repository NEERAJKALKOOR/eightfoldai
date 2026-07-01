"""Value normalizers.

Pure functions that convert extracted values into canonical formats (E.164
phones, YYYY-MM dates, ISO-3166 alpha-2 countries, canonical skill names,
normalized emails), each returning a value-or-null plus a normalization quality.

Each normalizer is deterministic, never raises on bad input, and returns the
canonical null result ``(None, 0.0)`` when a value cannot be converted. The
``normalization_quality`` (the second tuple element) is a float in ``[0.0, 1.0]``
that feeds the confidence formula (Req 3.10, 7.2).
"""

from __future__ import annotations

from .common import NULL_RESULT, NormalizationResult, clamp_quality
from .country import normalize_country
from .date import normalize_date
from .email import normalize_email
from .phone import normalize_phone
from .skills import Controlled_Skill_Vocabulary, normalize_skill

__all__ = [
    "NormalizationResult",
    "NULL_RESULT",
    "clamp_quality",
    "normalize_phone",
    "normalize_date",
    "normalize_country",
    "normalize_email",
    "normalize_skill",
    "Controlled_Skill_Vocabulary",
]

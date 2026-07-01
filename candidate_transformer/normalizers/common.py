"""Shared types and helpers for the value normalizers.

Every normalizer is a *pure* function that converts a raw input into a canonical
format and reports how cleanly the conversion went. The shared contract is the
:data:`NormalizationResult` tuple ``(value | None, normalization_quality)`` where
``normalization_quality`` is a float in ``[0.0, 1.0]`` (Req 3.10) that later feeds
the confidence formula (Req 7.2).

Normalizers must be **deterministic** and must **never raise** on bad input: an
unconvertible value yields the canonical *null result* ``(None, 0.0)`` rather than
an exception or an invented value (the guiding "honestly-empty over
wrong-but-confident" principle).
"""

from __future__ import annotations

from typing import Optional, Tuple

# A normalizer's return value: the canonical value (or ``None``) plus its
# Normalization_Quality score in ``[0.0, 1.0]``.
NormalizationResult = Tuple[Optional[object], float]

# The canonical "could not normalize" result. Using a module-level constant keeps
# the null contract identical across every normalizer.
NULL_RESULT: NormalizationResult = (None, 0.0)


def clamp_quality(quality: float) -> float:
    """Clamp a quality score into the closed interval ``[0.0, 1.0]`` (Req 3.10)."""
    if quality < 0.0:
        return 0.0
    if quality > 1.0:
        return 1.0
    return quality

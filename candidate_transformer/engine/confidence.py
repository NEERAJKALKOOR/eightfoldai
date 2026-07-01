"""The confidence model (Req 7).

This module holds the *pure* confidence-scoring primitives used by the merge stage
to attach a ``Field_Confidence`` to every canonical value and an
``Overall_Confidence`` to the assembled record. The functions here are deliberately
free of any merge/winner-selection logic (that lives in the merge module, task 8.x):
they are deterministic, side-effect-free numeric helpers that the merge stage calls.

The design's confidence model (Req 7):

* ``Confidence_Formula`` (Req 7.2) -- applied per field and clamped to ``[0, 1]``::

      Field_Confidence = clamp(0.5 * Source_Reliability
                             + 0.3 * Agreement_Score
                             + 0.2 * Normalization_Quality, 0.0, 1.0)

* ``Agreement_Score`` (Req 7.4)::

      Agreement_Score = (# sources supplying the winning value)
                        / (# sources containing the field)
                      = 0.0  when no source contains the field  (avoids /0)

* ``Source_Reliability`` weights (Req 7.3) live in
  :mod:`candidate_transformer.adapters.base` and are looked up via
  :func:`~candidate_transformer.adapters.base.reliability_of`.

* A ``null`` field gets ``Field_Confidence = 0.0`` (Req 7.7).

* ``Overall_Confidence`` (Req 7.6) is the mean of the ``Field_Confidence`` values of
  the non-null canonical fields, or ``0.0`` when every field is null.

Because more agreeing sources raises ``Agreement_Score`` (and nothing else in the
formula decreases), multi-source agreement yields a confidence greater than or equal
to single-source confidence -- satisfying the monotonicity requirement (Req 7.5).

All functions are deterministic: identical inputs always produce identical outputs
(Req 7.8).
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "RELIABILITY_WEIGHT",
    "AGREEMENT_WEIGHT",
    "QUALITY_WEIGHT",
    "clamp_unit",
    "agreement_score",
    "field_confidence",
    "null_field_confidence",
    "overall_confidence",
]


# ---------------------------------------------------------------------------
# Confidence_Formula weights (Req 7.2)
# ---------------------------------------------------------------------------

#: Weight applied to Source_Reliability in the Confidence_Formula.
RELIABILITY_WEIGHT: float = 0.5
#: Weight applied to Agreement_Score in the Confidence_Formula.
AGREEMENT_WEIGHT: float = 0.3
#: Weight applied to Normalization_Quality in the Confidence_Formula.
QUALITY_WEIGHT: float = 0.2


def clamp_unit(value: float) -> float:
    """Clamp ``value`` into the closed unit interval ``[0.0, 1.0]``.

    Values below ``0.0`` become ``0.0`` and values above ``1.0`` become ``1.0``.
    Used to bound the Confidence_Formula result (Req 7.2).
    """
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def agreement_score(num_supplying_winner: int, num_containing_field: int) -> float:
    """Compute the ``Agreement_Score`` for a field (Req 7.4).

    ``Agreement_Score`` is the fraction of sources containing the field that supply
    the selected/winning value::

        agreement = num_supplying_winner / num_containing_field

    Parameters
    ----------
    num_supplying_winner:
        Number of sources that supply the selected/winning value.
    num_containing_field:
        Number of sources that contain the field at all.

    Returns
    -------
    float
        The agreement score in ``[0.0, 1.0]``. When ``num_containing_field`` is
        ``0`` (no source contains the field) the score is ``0.0``, avoiding a
        divide-by-zero (Req 7.4).
    """
    if num_containing_field <= 0:
        return 0.0
    return clamp_unit(num_supplying_winner / num_containing_field)


def field_confidence(
    reliability: float,
    agreement: float,
    quality: float,
) -> float:
    """Compute a single value's ``Field_Confidence`` via the Confidence_Formula.

    Applies ``clamp(0.5 * reliability + 0.3 * agreement + 0.2 * quality, 0, 1)``
    (Req 7.2). All three inputs are expected to lie in ``[0, 1]``; the clamp keeps
    the result bounded even if an input strays outside that range.

    Parameters
    ----------
    reliability:
        Source_Reliability weight of the winning value's source (Req 7.3).
    agreement:
        Agreement_Score for the field (see :func:`agreement_score`) (Req 7.4).
    quality:
        Normalization_Quality of the winning value (Req 3.10).

    Returns
    -------
    float
        The clamped Field_Confidence in ``[0.0, 1.0]``.
    """
    raw = (
        RELIABILITY_WEIGHT * reliability
        + AGREEMENT_WEIGHT * agreement
        + QUALITY_WEIGHT * quality
    )
    return clamp_unit(raw)


def null_field_confidence() -> float:
    """Return the ``Field_Confidence`` for a null-valued field: exactly ``0.0``.

    A field whose value could not be determined is recorded as ``null`` and carries
    a confidence of ``0.0`` -- an honestly-empty value is never given any certainty
    (Req 7.7).
    """
    return 0.0


def overall_confidence(field_confidences: Iterable[float | None]) -> float:
    """Compute the ``Overall_Confidence`` of a Canonical_Record (Req 7.6).

    ``Overall_Confidence`` is the arithmetic mean of the ``Field_Confidence`` values
    of the record's *non-null* fields. A null field is signalled by a ``None`` entry
    and is the only thing excluded from the mean. A *present* field is always
    included, even on the (rare) occasion its score is exactly ``0.0`` -- excluding a
    real-but-low score would silently inflate the overall number, which runs against
    the project's core principle that a wrong-but-confident value is worse than an
    honestly-empty one. When every field is null (all ``None`` / no entries) the
    overall confidence is ``0.0``.

    Null is deliberately keyed on ``None`` rather than on a ``0.0`` value so the
    contract is unambiguous: the caller marks a missing field by passing ``None``
    and a present field by passing its score. (Per Req 7.7 a null field also *scores*
    ``0.0``, but that zero is carried as ``None`` here so it is never confused with a
    present field that merely scored low.)

    Parameters
    ----------
    field_confidences:
        The per-field confidences of the record. ``None`` entries mark null fields
        and are skipped; every non-``None`` entry is a present field and is included
        in the mean, including a present ``0.0``.

    Returns
    -------
    float
        The mean of the present field confidences in ``[0.0, 1.0]``, or ``0.0`` when
        there are none.
    """
    contributing = [c for c in field_confidences if c is not None]
    if not contributing:
        return 0.0
    return clamp_unit(sum(contributing) / len(contributing))

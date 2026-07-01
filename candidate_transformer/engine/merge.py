"""Merge & conflict resolution (Req 5).

This module is the start of the Merge_Module. It implements the
``Winner_Selection_Policy`` comparator for **single-valued** canonical fields
(task 8.1). Later tasks layer list-value deduplication and provenance tracking
(task 8.2) on top of the same module, so the design here keeps the candidate-value
representation and the comparator small, pure, and reusable.

Winner_Selection_Policy (Req 5.3) is an ordered comparator applied to the set of
candidate values collected for one single-valued field across an identity group's
sources. The winner is the first element after sorting by, in order:

1. **SourcePriority** -- prefer the more authoritative source type. Lower
   :func:`~candidate_transformer.adapters.base.priority_of` rank wins (Req 5.4).
2. **Field_Confidence** -- higher confidence wins.
3. **Normalization_Quality** -- cleaner conversion wins (Req 3.10).
4. **Stable lexical order** of the value's string form -- final deterministic
   tie-break (Req 5.5).

Because the comparator yields a *total* order over candidate values, the selected
winner is independent of the order in which candidates are presented: sorting any
permutation of the same candidates produces the same first element (Req 5.2).

All functions are pure and deterministic: identical inputs always produce identical
output, with no wall-clock or randomness.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from candidate_transformer.adapters.base import priority_of
from candidate_transformer.models import FieldValue, Links, PerSourceRecord, ProvenanceEntry

__all__ = [
    "CandidateValue",
    "winner_sort_key",
    "select_winner",
    # list-value dedup + provenance (task 8.2)
    "LIST_VALUED_FIELDS",
    "NOT_FOUND_METHOD",
    "ListContribution",
    "ListFieldResult",
    "extract_list_items",
    "list_contributions_for_field",
    "list_value_sort_key",
    "dedup_list_values",
    "combine_list_field",
    "make_provenance",
    "not_found_provenance",
    "order_provenance",
]


@dataclass(frozen=True)
class CandidateValue:
    """One candidate value for a single-valued field, with its ranking inputs.

    A small, immutable representation capturing everything the
    ``Winner_Selection_Policy`` comparator needs to order candidate values. It is
    deliberately decoupled from ``PerSourceRecord`` so the comparator stays pure and
    easy to test; the merge stage (task 8.2) constructs these from per-source records.

    Attributes
    ----------
    value:
        The candidate value itself (any canonical scalar). Its string form is used
        as the final lexical tie-breaker, so it must be representable via ``str``.
    source_type:
        The originating source type (e.g. ``"recruiter_csv"``). Drives SourcePriority
        via :func:`~candidate_transformer.adapters.base.priority_of` (Req 5.4).
    field_confidence:
        The Field_Confidence assigned to this value, in ``[0, 1]``. Higher wins.
    normalization_quality:
        The Normalization_Quality of this value, in ``[0, 1]`` (Req 3.10). Higher
        wins, after Field_Confidence.
    source_id:
        Optional originating source identifier, retained for provenance in later
        tasks. Not used by the comparator.
    method:
        Optional extraction method, retained for provenance in later tasks. Not used
        by the comparator.
    """

    value: Any
    source_type: str
    field_confidence: float = 0.0
    normalization_quality: float = 0.0
    source_id: str | None = None
    method: str | None = None

    @classmethod
    def from_field_value(
        cls,
        field_value: FieldValue,
        *,
        source_type: str,
        field_confidence: float,
        source_id: str | None = None,
    ) -> "CandidateValue":
        """Build a :class:`CandidateValue` from a per-source :class:`FieldValue`.

        Convenience factory for the merge stage: carries the value, extraction
        method, and Normalization_Quality from ``field_value`` and pairs them with
        the supplied ``source_type`` and computed ``field_confidence``.
        """
        return cls(
            value=field_value.value,
            source_type=source_type,
            field_confidence=field_confidence,
            normalization_quality=field_value.normalization_quality,
            source_id=source_id,
            method=field_value.method,
        )


def winner_sort_key(candidate: CandidateValue) -> tuple[int, float, float, str]:
    """Return the ordered sort key implementing the Winner_Selection_Policy.

    The key sorts ascending such that the *best* candidate sorts first:

    1. ``priority_of(source_type)`` -- ascending (lower rank = more authoritative).
    2. ``-field_confidence`` -- so higher Field_Confidence sorts first.
    3. ``-normalization_quality`` -- so higher Normalization_Quality sorts first.
    4. ``str(value)`` -- ascending lexical order, the final deterministic tie-break.

    The key is a total order over candidate values, making winner selection
    independent of input ordering (Req 5.2, 5.3, 5.4, 5.5).
    """
    return (
        priority_of(candidate.source_type),
        -candidate.field_confidence,
        -candidate.normalization_quality,
        str(candidate.value),
    )


def select_winner(candidates: list[CandidateValue]) -> CandidateValue | None:
    """Select the winning candidate value via the Winner_Selection_Policy.

    Sorts ``candidates`` by :func:`winner_sort_key` and returns the first element.
    The result is deterministic and independent of the order in which candidates are
    presented (Req 5.2, 5.3, 5.4, 5.5).

    Parameters
    ----------
    candidates:
        The candidate values collected for one single-valued field across an identity
        group's sources.

    Returns
    -------
    CandidateValue | None
        The winning candidate, or ``None`` when ``candidates`` is empty.
    """
    if not candidates:
        return None
    return min(candidates, key=winner_sort_key)


# ===========================================================================
# List-value deduplication & provenance tracking (task 8.2)
# ===========================================================================
#
# Where single-valued fields are resolved by the Winner_Selection_Policy above,
# *list-valued* canonical fields (emails, phones, skills, links.other) are handled
# differently: their values are **combined across all of an identity group's
# sources, deduplicated, and sorted into a deterministic order** for stable output
# (Req 5.6, 12.2). Every contributing value -- one per list element per source --
# keeps its own provenance entry (Req 6.4), and a field that *no* source supplied
# gets a single ``value=null`` "not found" provenance entry (Req 6.3).
#
# The building blocks here are pure and deterministic so the engine orchestration
# (task 14.1) can call them to assemble a Canonical_Record. Nothing in this section
# reaches for wall-clock time or randomness, so identical inputs always yield
# identical lists and identical provenance ordering (Req 12.2, 12.3).


#: The canonical fields whose values are combined into a deduplicated list rather
#: than resolved to a single winner (Req 5.6). ``links.other`` is addressed by its
#: dotted path because it lives inside the ``links`` sub-structure.
LIST_VALUED_FIELDS: tuple[str, ...] = ("emails", "phones", "skills", "links.other")

#: The ``method`` recorded on a "not found" provenance entry for a field that no
#: source provided (Req 6.3).
NOT_FOUND_METHOD: str = "not_found"


@dataclass(frozen=True)
class ListContribution:
    """One source's contribution of a single value to a list-valued field.

    A list-valued field collects many of these across an identity group's sources:
    if a source supplies three skills it produces three :class:`ListContribution`
    objects, one per skill. Each contribution is the unit that becomes a provenance
    entry (Req 6.4), so it carries everything a :class:`ProvenanceEntry` needs.

    Attributes
    ----------
    value:
        The individual (already-normalized) list element, e.g. one email address or
        one Canonical_Skill_Name. Never ``None`` -- absent values are not contributed.
    source_type:
        The originating source type (e.g. ``"resume"``). Retained for deterministic
        provenance tie-breaking via SourcePriority.
    source_id:
        The originating Source identifier recorded as the provenance ``source``.
    method:
        The extraction method used to derive the value (provenance ``method``).
    field_confidence:
        The Field_Confidence assigned to this contributing value, in ``[0, 1]``.
    """

    value: Any
    source_type: str
    source_id: str | None = None
    method: str | None = None
    field_confidence: float = 0.0


@dataclass(frozen=True)
class ListFieldResult:
    """The merged outcome for one list-valued field.

    ``values`` is the deduplicated, deterministically ordered list placed into the
    Canonical_Record (Req 5.6, 12.2). ``provenance`` holds one entry per contributing
    value -- or a single ``value=null`` "not found" entry when no source supplied the
    field -- in a deterministic order (Req 6.3, 6.4, 12.3).
    """

    field: str
    values: list[Any] = field(default_factory=list)
    provenance: list[ProvenanceEntry] = field(default_factory=list)


def extract_list_items(field_name: str, field_value: FieldValue | None) -> list[Any]:
    """Flatten one source's :class:`FieldValue` into individual list items.

    Per-source adapters store a list-valued field either as a scalar (e.g. a single
    email in ``emails``), as a list (e.g. several ``skills``), or -- for
    ``links.other`` -- inside a :class:`~candidate_transformer.models.Links` object.
    This helper normalizes all of those shapes into a flat list of contributable
    items, dropping ``None`` so absent values are never contributed (Req 2.6).

    Parameters
    ----------
    field_name:
        The canonical field name. ``"links.other"`` reads the ``other`` list from a
        :class:`Links` value; every other field reads the value directly.
    field_value:
        The per-source :class:`FieldValue` for the field, or ``None`` if absent.

    Returns
    -------
    list[Any]
        The individual, non-null items contributed by this field value (possibly
        empty). Order follows the source's own order.
    """
    if field_value is None:
        return []
    raw = field_value.value
    if raw is None:
        return []

    if field_name == "links.other":
        # ``links`` is stored as a Links object; only its ``other`` list is list-valued.
        if isinstance(raw, Links):
            other = raw.other or []
            return [item for item in other if item is not None]
        # Defensive: a bare list under links.other is still usable.
        if isinstance(raw, list):
            return [item for item in raw if item is not None]
        return [raw]

    if isinstance(raw, list):
        return [item for item in raw if item is not None]
    return [raw]


def _field_value_for(field_name: str, record: PerSourceRecord) -> FieldValue | None:
    """Return the :class:`FieldValue` backing ``field_name`` in ``record``.

    Resolves the ``links.other`` dotted path to the record's ``links`` entry; all
    other fields map directly to their key in ``record.values``.
    """
    key = "links" if field_name == "links.other" else field_name
    return record.values.get(key)


def list_contributions_for_field(
    field_name: str,
    records: Iterable[PerSourceRecord],
    *,
    confidence_for: Callable[[str, PerSourceRecord, Any], float] | None = None,
) -> list[ListContribution]:
    """Build the :class:`ListContribution` set for ``field_name`` across ``records``.

    Walks every per-source record, flattens its value for the field into individual
    items (see :func:`extract_list_items`), and pairs each item with its source
    provenance. The optional ``confidence_for`` callback supplies the per-value
    Field_Confidence; when omitted, contributions carry ``0.0`` (the engine wires in
    real confidences in task 14.1).

    Parameters
    ----------
    field_name:
        The canonical list-valued field (one of :data:`LIST_VALUED_FIELDS`).
    records:
        The identity group's per-source records.
    confidence_for:
        Optional ``(field_name, record, item) -> float`` callback for the value's
        Field_Confidence.

    Returns
    -------
    list[ListContribution]
        One contribution per contributing list item, in source/record order.
    """
    contributions: list[ListContribution] = []
    for record in records:
        field_value = _field_value_for(field_name, record)
        items = extract_list_items(field_name, field_value)
        method = field_value.method if field_value is not None else None
        for item in items:
            confidence = (
                confidence_for(field_name, record, item)
                if confidence_for is not None
                else 0.0
            )
            contributions.append(
                ListContribution(
                    value=item,
                    source_type=record.source_type,
                    source_id=record.source_id,
                    method=method,
                    field_confidence=confidence,
                )
            )
    return contributions


def list_value_sort_key(value: Any) -> tuple[str, str]:
    """Return a deterministic, total-order sort key for a list value (Req 12.2).

    Keys on the value's type name first, then its string form, so a list mixing
    types (defensive only -- canonical list fields are homogeneous strings) still
    sorts into a single stable order independent of input ordering.
    """
    return (type(value).__name__, str(value))


def dedup_list_values(values: Iterable[Any]) -> list[Any]:
    """Deduplicate ``values`` and return them in a deterministic sorted order.

    Duplicates are collapsed by value equality and the survivors are ordered by
    :func:`list_value_sort_key`. The result is independent of the order in which
    values are supplied, satisfying the stable-ordering requirement for list-valued
    fields (Req 5.6, 12.2).
    """
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return sorted(seen, key=list_value_sort_key)


def make_provenance(
    field_name: str,
    value: Any,
    source: str | None,
    method: str | None,
    confidence: float,
) -> ProvenanceEntry:
    """Construct a :class:`ProvenanceEntry` ``{field, value, source, method, confidence}``.

    A thin, explicit constructor (Req 6.1) used for both single-valued winners and
    each contributing list value.
    """
    return ProvenanceEntry(
        field=field_name,
        value=value,
        source=source,
        method=method,
        confidence=confidence,
    )


def not_found_provenance(field_name: str) -> ProvenanceEntry:
    """Return the ``value=null`` "not found" provenance entry for ``field_name`` (Req 6.3).

    When no source provided a field, the record still carries an honest explanation:
    a provenance entry with ``value=None``, no ``source``, and a ``method`` of
    :data:`NOT_FOUND_METHOD` indicating the value was not found (Req 6.3, 17.2).
    """
    return make_provenance(
        field_name,
        value=None,
        source=None,
        method=NOT_FOUND_METHOD,
        confidence=0.0,
    )


def _provenance_order_key(entry: ProvenanceEntry) -> tuple[str, str, str]:
    """Deterministic ordering key for provenance entries (Req 12.3).

    Orders by the value's string form, then by source id, then by method -- a total
    order that is independent of the order in which entries were produced.
    """
    return (
        str(entry.value),
        entry.source or "",
        entry.method or "",
    )


def order_provenance(entries: Iterable[ProvenanceEntry]) -> list[ProvenanceEntry]:
    """Return ``entries`` in a deterministic, input-order-independent order (Req 12.3)."""
    return sorted(entries, key=_provenance_order_key)


def combine_list_field(
    field_name: str,
    contributions: Iterable[ListContribution],
) -> ListFieldResult:
    """Combine, dedup, and build provenance for one list-valued field (task 8.2).

    This is the reusable heart of list-field merging:

    * **Combine + dedup + sort** every contributed value into a single
      deterministically ordered, duplicate-free list (Req 5.6, 12.2).
    * **Provenance** -- record one entry per *contributing* value, preserving each
      source's lineage even when two sources agree on the same value (Req 6.4); the
      entries are returned in a deterministic order (Req 12.3).
    * **Not-found** -- when there are no contributions (no source supplied the field),
      emit a single ``value=null`` "not found" provenance entry (Req 6.3).

    Parameters
    ----------
    field_name:
        The canonical list-valued field name.
    contributions:
        The :class:`ListContribution` objects gathered across the group's sources
        (see :func:`list_contributions_for_field`).

    Returns
    -------
    ListFieldResult
        The deduplicated ordered ``values`` and the ordered ``provenance`` entries.
    """
    contribution_list = list(contributions)

    if not contribution_list:
        return ListFieldResult(
            field=field_name,
            values=[],
            provenance=[not_found_provenance(field_name)],
        )

    values = dedup_list_values(c.value for c in contribution_list)
    provenance = order_provenance(
        make_provenance(
            field_name,
            value=c.value,
            source=c.source_id,
            method=c.method,
            confidence=c.field_confidence,
        )
        for c in contribution_list
    )
    return ListFieldResult(field=field_name, values=values, provenance=provenance)

"""Property-based test for identity grouping.

Feature: candidate-data-transformer, Property 9

Property 9: Identity grouping is order-independent and consistent.

For any set of per-source records, grouping is independent of input order, and any
two records that share a normalized email (or satisfy any earlier rule in
``Identity_Match_Priority``) are assigned to the same identity group; records
satisfying no rule are placed in separate groups.

The minimum of 100 iterations is provided by the project's default Hypothesis
``ci`` profile (registered in ``tests/conftest.py`` with ``max_examples=100``).

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**
"""

from __future__ import annotations

import random

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine.identity import (
    _IdentityKeys,
    _matched_rule,
    group,
)
from candidate_transformer.models import FieldValue, PerSourceRecord
from candidate_transformer.normalizers import normalize_email, normalize_phone


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
# Small pools drawn from intentionally so that collisions happen often. Emails and
# phones include case/format variants that normalize to the *same* canonical value
# (``BOB@Example.com``/``bob@example.com`` -> ``bob@example.com``; the two phone
# spellings -> ``+14155550001``) so the test exercises normalized equality, not raw
# string equality. The four names are mutually dissimilar (RapidFuzz ratio well
# below the 0.9 threshold), so two distinct names never spuriously match on rule 5;
# an *identical* name string repeated does match rule 5, which is intended.
_EMAILS = st.sampled_from(
    [
        "alice@example.com",
        "BOB@Example.com",
        "bob@example.com",
        "carol@example.com",
        None,
    ]
)
_PHONES = st.sampled_from(
    [
        "+14155550001",
        "+1 (415) 555-0001",
        "+14155550002",
        None,
    ]
)
_NAMES = st.sampled_from(
    [
        "Alice Anderson",
        "Bob Brown",
        "Carol Clark",
        "Dave Davis",
        None,
    ]
)

# One record spec is an (email, phone, name) triple; each field may be absent.
_record_spec = st.tuples(_EMAILS, _PHONES, _NAMES)
_record_specs = st.lists(_record_spec, min_size=0, max_size=8)


def _build_records(
    specs: list[tuple[str | None, str | None, str | None]],
) -> list[PerSourceRecord]:
    """Build records with unique source_ids from generated (email, phone, name) specs."""
    records: list[PerSourceRecord] = []
    for index, (email, phone, name) in enumerate(specs):
        values: dict[str, FieldValue] = {}
        if name is not None:
            values["full_name"] = FieldValue(value=name, method="test")
        if email is not None:
            values["emails"] = FieldValue(value=email, method="test")
        if phone is not None:
            values["phones"] = FieldValue(value=phone, method="test")
        records.append(
            PerSourceRecord(
                source_id=f"r{index}", source_type="recruiter_csv", values=values
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _partition(groups: list[list[PerSourceRecord]]) -> set[frozenset[str]]:
    """Represent a grouping as a set of frozensets of source_ids (order-agnostic)."""
    return {frozenset(rec.source_id for rec in grp) for grp in groups}


def _group_of(source_id: str, groups: list[list[PerSourceRecord]]) -> frozenset[str]:
    """Return the (frozenset of source_ids of the) group containing ``source_id``."""
    for grp in groups:
        ids = frozenset(rec.source_id for rec in grp)
        if source_id in ids:
            return ids
    raise AssertionError(f"{source_id!r} missing from every group")


def _normalized_email(record: PerSourceRecord) -> str | None:
    """Independently compute the record's normalized email (None if absent/empty)."""
    field = record.values.get("emails")
    if field is None:
        return None
    value, _quality = normalize_email(field.value)
    return value


def _normalized_phone(record: PerSourceRecord) -> str | None:
    """Independently compute the record's normalized phone (None if absent/empty)."""
    field = record.values.get("phones")
    if field is None:
        return None
    value, _quality = normalize_phone(field.value)
    return value


def _oracle_partition(records: list[PerSourceRecord]) -> set[frozenset[str]]:
    """Independent reference partition: transitive closure of the rule relation.

    Builds connected components over the pairwise ``Identity_Match_Priority``
    relation directly (union by rule, transitive). This is the expected partition:
    matching pairs land together, unmatched records stay separate.
    """
    keys = [_IdentityKeys.of(rec) for rec in records]
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for i in range(n):
        for j in range(i + 1, n):
            if _matched_rule(keys[i], keys[j]) is not None:
                union(i, j)

    components: dict[int, set[str]] = {}
    for i, rec in enumerate(records):
        components.setdefault(find(i), set()).add(rec.source_id)
    return {frozenset(ids) for ids in components.values()}


# --------------------------------------------------------------------------- #
# Property
# --------------------------------------------------------------------------- #
@given(specs=_record_specs, seed=st.integers(min_value=0, max_value=10_000))
def test_identity_grouping_is_order_independent_and_consistent(
    specs: list[tuple[str | None, str | None, str | None]], seed: int
) -> None:
    records = _build_records(specs)
    groups = group(records)
    partition = _partition(groups)

    # The result is a valid partition: every record appears exactly once.
    flattened = [rec.source_id for grp in groups for rec in grp]
    assert sorted(flattened) == sorted(rec.source_id for rec in records)
    assert len(flattened) == len(set(flattened))

    # (Req 4.1) Order independence: shuffling the input yields the same partition.
    shuffled = records[:]
    random.Random(seed).shuffle(shuffled)
    assert _partition(group(shuffled)) == partition

    # (Req 4.2 rule 1, 4.3) Any two records sharing a normalized email are grouped
    # together -- verified with an independent normalization of the raw email.
    for i, rec_i in enumerate(records):
        email_i = _normalized_email(rec_i)
        phone_i = _normalized_phone(rec_i)
        for rec_j in records[i + 1 :]:
            if email_i is not None and email_i == _normalized_email(rec_j):
                assert _group_of(rec_i.source_id, groups) == _group_of(
                    rec_j.source_id, groups
                )
            # (Req 4.2 rule 2, 4.3) Shared normalized phone -> same group.
            if phone_i is not None and phone_i == _normalized_phone(rec_j):
                assert _group_of(rec_i.source_id, groups) == _group_of(
                    rec_j.source_id, groups
                )

    # (Req 4.2-4.5) The full partition equals the transitive closure of the
    # Identity_Match_Priority relation: pairs that satisfy a rule are grouped, and
    # records satisfying no rule with anyone are placed in separate groups.
    assert partition == _oracle_partition(records)

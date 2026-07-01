"""Identity resolution: group per-source records that refer to one candidate.

This module implements the **grouping** half of the Identity_Resolver (Req 4.1-4.5,
design "Identity Resolution"). It decides whether two :class:`PerSourceRecord`s
refer to the same person by applying the fixed-order **Identity_Match_Priority**
rules, stopping at the first satisfied rule:

1. exact normalized email match,
2. exact normalized phone match,
3. exact normalized email **and** full-name match,
4. exact normalized phone **and** full-name match,
5. full-name similarity greater than ``0.9`` (the RapidFuzz ratio, on a 0-1 scale),
   **provided no email/phone identifier contradicts the match** (see below).

Note on rules 3 and 4: under the spec's own first-match-wins ordering (Req 4.5),
these are *strict refinements* of rules 1 and 2 -- their condition (a shared
email/phone **and** a matching name) can only hold when the shared-email/phone
condition of rule 1/2 already holds, so rule 1 or 2 always fires first. Rules 3 and
4 are therefore unreachable as the "first satisfied" rule, and the three operative
match keys are exact email, exact phone, and (guarded) fuzzy full-name. We document
this subsumption explicitly rather than carry branches that can never execute (see
:func:`_matched_rule`).

Note on rule 5 (a deliberate refinement of the spec): a shared name is the weakest
identity signal, so we merge on name similarity **only when no strong contact
identifier contradicts it**. If both records carry an email, or both carry a phone,
and those identifiers do not overlap, the records are treated as different people
even when the names match -- distinct contact details are positive evidence of
distinct people. The same person is still merged across sources that lack a shared
contact channel (no contradiction). See :func:`_contacts_conflict`.

Two records that satisfy *any* rule are placed in the same identity group; the
relation is made transitive with a **union-find** (disjoint-set) structure, so if
A matches B and B matches C then A, B and C all share one group even when A and C
do not match directly (Req 4.3). Records that satisfy no rule end up in separate
groups (Req 4.4).

Determinism / order-independence (Req 4.1, design): records are sorted by a stable
key before pairwise evaluation, every unordered pair is considered, and groups (and
the records within them) are emitted in a deterministic order. The resulting
partition therefore does not depend on the order the records were supplied in.

Scope: this task (6.1) produces the *grouping*. Deterministic ``candidate_id``
assignment (Req 4.6-4.8) is implemented here too (task 6.2): see
:func:`candidate_id_for_group` and :func:`assign_candidate_ids`, which consume the
groups produced by :meth:`IdentityResolver.group`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz

from candidate_transformer.models import PerSourceRecord
from candidate_transformer.normalizers import normalize_email, normalize_phone

__all__ = [
    "IdentityResolver",
    "group",
    "NAME_SIMILARITY_THRESHOLD",
    "NAMESPACE_CANDIDATE",
    "candidate_id_for_group",
    "assign_candidate_ids",
]

#: Full-name similarity threshold (Req 4.2 rule 5). The RapidFuzz ratio is a
#: normalized Levenshtein similarity in ``[0, 100]``; the requirement's ">0.9" on
#: the 0-1 scale corresponds to a ratio strictly greater than ``90.0``.
NAME_SIMILARITY_THRESHOLD = 90.0

#: Fixed, content-derived namespace UUID for candidate_id derivation (Req 4.7).
#: This is a stable, hardcoded constant so that ``UUID5(NAMESPACE_CANDIDATE, key)``
#: is reproducible across runs, machines, and processes -- no wall-clock or
#: randomness ever feeds the id. The literal value below was generated once and is
#: now frozen; changing it would change every candidate_id, so it must not be
#: edited.
NAMESPACE_CANDIDATE = uuid.UUID("6f9b3f7a-2c1e-5d8b-9a4c-3e2d1f0a7b6c")


# --------------------------------------------------------------------------- #
# Union-Find (disjoint set) for transitive grouping (Req 4.3)
# --------------------------------------------------------------------------- #
class _UnionFind:
    """A minimal disjoint-set structure with path compression + union by rank.

    Operates on integer element ids ``0 .. n-1``. Used to make the pairwise match
    relation transitive: ``union(a, b)`` merges the sets containing ``a`` and ``b``.
    """

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


# --------------------------------------------------------------------------- #
# Identity-key extraction helpers
# --------------------------------------------------------------------------- #
def _field_value(record: PerSourceRecord, key: str) -> object:
    """Return the raw ``.value`` stored under ``key`` in ``record.values`` (or None)."""
    field = record.values.get(key)
    return field.value if field is not None else None


def _normalized_emails(record: PerSourceRecord) -> frozenset[str]:
    """Return the set of normalized emails the record carries (possibly empty).

    Adapters store a single scalar email under ``values["emails"]``, but this
    tolerates a list too (a later merge stage may produce one). Each candidate
    value is normalized with :func:`normalize_email`; only non-null results count.
    """
    raw = _field_value(record, "emails")
    candidates = raw if isinstance(raw, (list, tuple)) else [raw]
    out: set[str] = set()
    for item in candidates:
        value, _quality = normalize_email(item)
        if value is not None:
            out.add(value)
    return frozenset(out)


def _normalized_phones(record: PerSourceRecord) -> frozenset[str]:
    """Return the set of normalized (E.164) phones the record carries (possibly empty)."""
    raw = _field_value(record, "phones")
    candidates = raw if isinstance(raw, (list, tuple)) else [raw]
    out: set[str] = set()
    for item in candidates:
        value, _quality = normalize_phone(item)
        if value is not None:
            out.add(value)
    return frozenset(out)


def _normalized_name(record: PerSourceRecord) -> str | None:
    """Return a normalized full-name key (lowercased, whitespace-collapsed) or None.

    Used both for the exact full-name comparison in rules 3-4 and as the input to
    the RapidFuzz similarity in rule 5. Returns ``None`` when the record has no
    usable name so name-dependent rules cannot spuriously match on emptiness.
    """
    raw = _field_value(record, "full_name")
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split()).lower()
    return collapsed or None


@dataclass(frozen=True)
class _IdentityKeys:
    """Pre-computed normalized identity keys for one record (computed once)."""

    emails: frozenset[str]
    phones: frozenset[str]
    name: str | None

    @classmethod
    def of(cls, record: PerSourceRecord) -> "_IdentityKeys":
        return cls(
            emails=_normalized_emails(record),
            phones=_normalized_phones(record),
            name=_normalized_name(record),
        )


def _names_similar(a: _IdentityKeys, b: _IdentityKeys) -> bool:
    """True when both names exist and their RapidFuzz ratio exceeds the threshold."""
    if a.name is None or b.name is None:
        return False
    return fuzz.ratio(a.name, b.name) > NAME_SIMILARITY_THRESHOLD


def _contacts_conflict(a: _IdentityKeys, b: _IdentityKeys) -> bool:
    """True when the records carry *contradicting* contact identifiers.

    A conflict means both records have a contact identifier of the same kind (email
    or phone) and those identifiers do **not** overlap -- positive evidence that the
    two records describe *different* people. This is used to guard the weak
    name-similarity rule: a shared name is not enough to merge two records when their
    emails or phones actively disagree.

    Note this is only ever consulted for rule 5, which is reached only after rules 1
    and 2 have already failed (no shared email, no shared phone). At that point
    "both have emails" necessarily means "both have *different* emails", so the guard
    reduces to: do both sides independently carry an email (or a phone)? If so, their
    contact details contradict and a name-only match is rejected.
    """
    email_conflict = bool(a.emails) and bool(b.emails) and not (a.emails & b.emails)
    phone_conflict = bool(a.phones) and bool(b.phones) and not (a.phones & b.phones)
    return email_conflict or phone_conflict


def _matched_rule(a: _IdentityKeys, b: _IdentityKeys) -> int | None:
    """Return the first Identity_Match_Priority rule (1, 2 or 5) satisfied, else ``None``.

    Req 4.2 enumerates five rules in a fixed, first-match-wins order: (1) exact
    email, (2) exact phone, (3) exact email + full-name, (4) exact phone +
    full-name, (5) full-name similarity > 0.9. Rules 3 and 4 are strict refinements
    of rules 1 and 2 (they add a name condition on top of the same shared
    email/phone), so they can only hold when rule 1 or 2 already holds -- and since
    those are evaluated first, rule 1/2 always wins. Rules 3 and 4 are therefore
    unreachable as a "first satisfied" rule, so we intentionally do not evaluate
    them: the operative match keys are exact email, exact phone, and fuzzy
    full-name. This returns the spec rule number that fired so callers can both
    decide a match *and* explain it; ``None`` means the records refer to different
    candidates (Req 4.4).

    Rule 5 refinement: a shared name is the weakest identity signal -- two different
    people commonly share a name. We therefore merge on name similarity **only when
    no strong contact identifier contradicts it** (see :func:`_contacts_conflict`):
    if both records carry an email or both carry a phone and those identifiers do
    not overlap, the name match is rejected and the records stay separate. This
    prevents false merges of distinct people who happen to share a name but have
    different contact details, while still merging the same person across sources
    that simply lack a shared contact channel.
    """
    # Rule 1: exact normalized email match (subsumes rule 3: email + name).
    if a.emails & b.emails:
        return 1
    # Rule 2: exact normalized phone match (subsumes rule 4: phone + name).
    if a.phones & b.phones:
        return 2
    # Rule 5: full-name similarity > 0.9 (RapidFuzz ratio), but only when no strong
    # contact identifier (email/phone) actively contradicts the name match.
    if _names_similar(a, b) and not _contacts_conflict(a, b):
        return 5
    return None


# --------------------------------------------------------------------------- #
# Stable ordering for order-independence (Req 4.1, design)
# --------------------------------------------------------------------------- #
def _stable_sort_key(record: PerSourceRecord) -> tuple:
    """A deterministic, content-derived sort key for a record.

    Sorting records before pairwise evaluation and group emission makes the output
    order-independent: the same set of records yields the same grouping and the
    same ordering regardless of how the caller ordered the input. The key is built
    from normalized identity content (smallest email, smallest phone, name) with
    ``source_type``/``source_id`` as final tie-breakers.
    """
    keys = _IdentityKeys.of(record)
    smallest_email = min(keys.emails) if keys.emails else ""
    smallest_phone = min(keys.phones) if keys.phones else ""
    return (
        smallest_email,
        smallest_phone,
        keys.name or "",
        str(record.source_type),
        str(record.source_id),
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
class IdentityResolver:
    """Groups :class:`PerSourceRecord`s that refer to the same candidate (Req 4.1-4.5).

    The resolver is stateless and deterministic. :meth:`group` returns the identity
    groups; deterministic ``candidate_id`` assignment is layered on top in a later
    task (see :func:`assign_candidate_ids`).
    """

    def group(self, records: list[PerSourceRecord]) -> list[list[PerSourceRecord]]:
        """Partition ``records`` into identity groups.

        Returns a list of groups, each a list of records that refer to the same
        candidate. Both the groups and the records within each group are emitted in
        a deterministic, order-independent order (Req 4.1).
        """
        if not records:
            return []

        # 1. Sort by a stable key so evaluation + output are order-independent.
        ordered = sorted(records, key=_stable_sort_key)
        keys = [_IdentityKeys.of(rec) for rec in ordered]

        # 2. Union every pair that satisfies any Identity_Match_Priority rule. The
        #    union-find makes the relation transitive (Req 4.3).
        uf = _UnionFind(len(ordered))
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                if _matched_rule(keys[i], keys[j]) is not None:
                    uf.union(i, j)

        # 3. Collect members by representative, preserving the stable order.
        groups_by_root: dict[int, list[PerSourceRecord]] = {}
        for index, record in enumerate(ordered):
            groups_by_root.setdefault(uf.find(index), []).append(record)

        # 4. Emit groups in a deterministic order (keyed by each group's first,
        #    already-stably-sorted, record).
        return [
            members
            for _root, members in sorted(
                groups_by_root.items(),
                key=lambda item: _stable_sort_key(item[1][0]),
            )
        ]


def group(records: list[PerSourceRecord]) -> list[list[PerSourceRecord]]:
    """Module-level convenience wrapper around :meth:`IdentityResolver.group`."""
    return IdentityResolver().group(records)


# --------------------------------------------------------------------------- #
# Deterministic candidate_id assignment (task 6.2, Req 4.6-4.8)
# --------------------------------------------------------------------------- #
def _group_identity_key(group_records: list[PerSourceRecord]) -> str:
    """Choose the deterministic normalized identity key for a group (Req 4.7).

    The key is selected by a fixed priority so identical group *content* always
    yields the same key regardless of record order:

    1. the lexicographically smallest normalized email across all records, else
    2. the lexicographically smallest normalized phone across all records, else
    3. the lexicographically smallest normalized-name key across all records, else
    4. a deterministic fallback derived from the group's stable identifiers
       (sorted ``source_type``/``source_id`` pairs) for the all-empty case.

    Each branch is prefixed (``email:`` / ``phone:`` / ``name:`` / ``fallback:``)
    so keys drawn from different branches can never collide.
    """
    keys = [_IdentityKeys.of(rec) for rec in group_records]

    emails: set[str] = set()
    phones: set[str] = set()
    names: set[str] = set()
    for k in keys:
        emails |= set(k.emails)
        phones |= set(k.phones)
        if k.name is not None:
            names.add(k.name)

    if emails:
        return "email:" + min(emails)
    if phones:
        return "phone:" + min(phones)
    if names:
        return "name:" + min(names)

    # All-empty fallback: derive a stable key from the group's identifiers so the
    # id is still deterministic and idempotent for identical content.
    identifiers = sorted(
        (str(rec.source_type), str(rec.source_id)) for rec in group_records
    )
    return "fallback:" + "|".join(f"{stype}/{sid}" for stype, sid in identifiers)


def candidate_id_for_group(group_records: list[PerSourceRecord]) -> str:
    """Derive a deterministic ``candidate_id`` for one identity group (Req 4.6-4.8).

    Picks the group's normalized identity key via :func:`_group_identity_key` and
    returns ``str(UUID5(NAMESPACE_CANDIDATE, identity_key))``. Because the key is a
    pure function of the group's normalized content (and the namespace is a fixed
    constant), the id is identical across repeated computation, across input
    reorderings, and whenever the same source content is processed again.
    """
    identity_key = _group_identity_key(group_records)
    return str(uuid.uuid5(NAMESPACE_CANDIDATE, identity_key))


def assign_candidate_ids(
    groups: list[list[PerSourceRecord]],
) -> list[tuple[str, list[PerSourceRecord]]]:
    """Assign one deterministic ``candidate_id`` to each identity group (Req 4.6-4.8).

    Returns a list of ``(candidate_id, group)`` pairs, one per input group and in
    the same order the groups were supplied. Each id is derived by
    :func:`candidate_id_for_group`, so identical content -> identical id and the
    mapping is idempotent across runs.
    """
    return [(candidate_id_for_group(grp), grp) for grp in groups]

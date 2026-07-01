"""Unit tests for Identity_Match_Priority grouping (Task 6.1).

Covers each of the five Identity_Match_Priority rules, transitive grouping via
union-find, order-independence of the resulting partition, and the separate-groups
case where no rule is satisfied.

_Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
"""

from __future__ import annotations

import random

from candidate_transformer.engine.identity import (
    IdentityResolver,
    _IdentityKeys,
    _matched_rule,
    group,
)
from candidate_transformer.models import FieldValue, PerSourceRecord


def make_record(
    source_id: str,
    *,
    source_type: str = "recruiter_csv",
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> PerSourceRecord:
    """Build a PerSourceRecord with scalar email/phone FieldValues (adapter shape)."""
    values: dict[str, FieldValue] = {}
    if full_name is not None:
        values["full_name"] = FieldValue(value=full_name, method="test")
    if email is not None:
        values["emails"] = FieldValue(value=email, method="test")
    if phone is not None:
        values["phones"] = FieldValue(value=phone, method="test")
    return PerSourceRecord(
        source_id=source_id, source_type=source_type, values=values
    )


def _partition(groups: list[list[PerSourceRecord]]) -> set[frozenset[str]]:
    """Represent a grouping as a set of frozensets of source_ids (order-agnostic)."""
    return {frozenset(rec.source_id for rec in grp) for grp in groups}


# --------------------------------------------------------------------------- #
# Rule 1: exact normalized email match
# --------------------------------------------------------------------------- #
def test_rule1_exact_email_groups_despite_different_names() -> None:
    a = make_record("a", full_name="Jane Doe", email="Jane.Doe@Example.com")
    b = make_record("b", full_name="J. Doe", email="jane.doe@example.com")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a", "b"})}


def test_rule1_is_the_first_matched_rule_for_shared_email() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="Jane", email="x@y.com"))
    b = _IdentityKeys.of(make_record("b", full_name="Jane", email="X@Y.COM"))
    assert _matched_rule(a, b) == 1


# --------------------------------------------------------------------------- #
# Rule 2: exact normalized phone match
# --------------------------------------------------------------------------- #
def test_rule2_exact_phone_groups_with_no_email_and_different_names() -> None:
    a = make_record("a", full_name="Bob Lee", phone="+14155552671")
    b = make_record("b", full_name="Robert Lee", phone="+1 (415) 555-2671")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a", "b"})}


def test_rule2_is_first_matched_rule_when_only_phone_shared() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="Bob", phone="+14155552671"))
    b = _IdentityKeys.of(make_record("b", full_name="Rob", phone="+14155552671"))
    assert _matched_rule(a, b) == 2


# --------------------------------------------------------------------------- #
# Rule 3: exact email + full-name match (email match dominates ordering)
# --------------------------------------------------------------------------- #
def test_rule3_email_and_name_match_groups_records() -> None:
    a = make_record("a", full_name="Jane Doe", email="jane@x.com")
    b = make_record("b", full_name="Jane Doe", email="jane@x.com")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a", "b"})}


# --------------------------------------------------------------------------- #
# Rule 4: exact phone + full-name match
# --------------------------------------------------------------------------- #
def test_rule4_phone_and_name_match_groups_records() -> None:
    a = make_record("a", full_name="Jane Doe", phone="+14155552671")
    b = make_record("b", full_name="Jane Doe", phone="+14155552671")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a", "b"})}


# --------------------------------------------------------------------------- #
# Rule 5: full-name similarity > 0.9 (RapidFuzz ratio), no email/phone overlap
# --------------------------------------------------------------------------- #
def test_rule5_similar_names_group_with_no_contact_overlap() -> None:
    a = make_record("a", full_name="John Smith")
    b = make_record("b", full_name="Jon Smith")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a", "b"})}


def test_rule5_is_first_matched_rule_for_similar_names_only() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="John Smith"))
    b = _IdentityKeys.of(make_record("b", full_name="Jon Smith"))
    assert _matched_rule(a, b) == 5


def test_dissimilar_names_do_not_match() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="John Smith"))
    b = _IdentityKeys.of(make_record("b", full_name="Maria Gonzalez"))
    assert _matched_rule(a, b) is None


# --------------------------------------------------------------------------- #
# Rule 5 guard: a shared name does NOT merge when contact details contradict
# --------------------------------------------------------------------------- #
def test_same_name_but_conflicting_email_and_phone_stay_separate() -> None:
    # Same person's name on both sources, but different email AND different phone:
    # the differing contact identifiers are evidence of two different people, so the
    # weak name match is rejected and the records are NOT grouped.
    a = make_record(
        "a", full_name="Neeraj Kalkoor", email="n1@x.com", phone="+919999999999"
    )
    b = make_record(
        "b", full_name="Neeraj Kalkoor", email="n2@x.com", phone="+918888888888"
    )
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a"}), frozenset({"b"})}


def test_same_name_conflicting_email_only_stays_separate() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="Jane Doe", email="jane@x.com"))
    b = _IdentityKeys.of(make_record("b", full_name="Jane Doe", email="jane@y.com"))
    assert _matched_rule(a, b) is None


def test_same_name_conflicting_phone_only_stays_separate() -> None:
    a = _IdentityKeys.of(make_record("a", full_name="Jane Doe", phone="+14155552671"))
    b = _IdentityKeys.of(make_record("b", full_name="Jane Doe", phone="+14155559999"))
    assert _matched_rule(a, b) is None


def test_same_name_groups_when_contact_present_on_one_side_only() -> None:
    # No contradiction: only one side has a contact identifier, so the name match
    # still merges (the same person across a contact-less source).
    a = _IdentityKeys.of(make_record("a", full_name="Jane Doe", email="jane@x.com"))
    b = _IdentityKeys.of(make_record("b", full_name="Jane Doe"))
    assert _matched_rule(a, b) == 5


# --------------------------------------------------------------------------- #
# Rule 4 (negative): no rule satisfied -> separate groups
# --------------------------------------------------------------------------- #
def test_no_rule_satisfied_yields_separate_groups() -> None:
    a = make_record("a", full_name="John Smith", email="john@x.com", phone="+14155552671")
    b = make_record("b", full_name="Maria Gonzalez", email="maria@y.com", phone="+442071838750")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a"}), frozenset({"b"})}


# --------------------------------------------------------------------------- #
# Transitivity via union-find (Req 4.3)
# --------------------------------------------------------------------------- #
def test_transitive_grouping_via_union_find() -> None:
    # A~B share an email; B~C share a phone; A and C share nothing directly.
    a = make_record("a", full_name="Jane Doe", email="jane@x.com")
    b = make_record("b", full_name="Jane Doe", email="jane@x.com", phone="+14155552671")
    c = make_record("c", full_name="Janie D", phone="+14155552671")
    groups = group([a, b, c])
    assert _partition(groups) == {frozenset({"a", "b", "c"})}


# --------------------------------------------------------------------------- #
# Order-independence (Req 4.1)
# --------------------------------------------------------------------------- #
def test_grouping_is_order_independent() -> None:
    records = [
        make_record("a", full_name="Jane Doe", email="jane@x.com"),
        make_record("b", full_name="J Doe", email="jane@x.com"),
        make_record("c", full_name="Bob Lee", phone="+14155552671"),
        make_record("d", full_name="Robert Lee", phone="+14155552671"),
        make_record("e", full_name="Maria Gonzalez", email="maria@y.com"),
    ]
    expected = _partition(group(records))
    rng = random.Random(1234)
    for _ in range(10):
        shuffled = records[:]
        rng.shuffle(shuffled)
        assert _partition(group(shuffled)) == expected
    # The three distinct people: {a,b}, {c,d}, {e}.
    assert expected == {
        frozenset({"a", "b"}),
        frozenset({"c", "d"}),
        frozenset({"e"}),
    }


# --------------------------------------------------------------------------- #
# Misc edge cases
# --------------------------------------------------------------------------- #
def test_empty_input_returns_no_groups() -> None:
    assert IdentityResolver().group([]) == []


def test_single_record_returns_one_group() -> None:
    rec = make_record("solo", full_name="Solo Person")
    assert _partition(IdentityResolver().group([rec])) == {frozenset({"solo"})}


def test_records_with_no_identity_keys_stay_separate() -> None:
    a = make_record("a")
    b = make_record("b")
    groups = group([a, b])
    assert _partition(groups) == {frozenset({"a"}), frozenset({"b"})}

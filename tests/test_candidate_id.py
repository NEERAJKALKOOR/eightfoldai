"""Unit tests for deterministic candidate_id assignment (Task 6.2).

Covers determinism (same content -> same id), idempotence across record/group
reordering, the email > phone > name identity-key priority, and the all-empty
no-keys deterministic fallback.

_Requirements: 4.6, 4.7, 4.8_
"""

from __future__ import annotations

import uuid

from candidate_transformer.engine.identity import (
    NAMESPACE_CANDIDATE,
    assign_candidate_ids,
    candidate_id_for_group,
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


# --------------------------------------------------------------------------- #
# Shape: candidate_id is a valid UUID5 in the fixed namespace
# --------------------------------------------------------------------------- #
def test_candidate_id_is_uuid5_in_fixed_namespace() -> None:
    grp = [make_record("a", full_name="Jane Doe", email="jane@x.com")]
    cid = candidate_id_for_group(grp)
    # Parses as a UUID and equals UUID5(namespace, "email:jane@x.com").
    assert uuid.UUID(cid).version == 5
    assert cid == str(uuid.uuid5(NAMESPACE_CANDIDATE, "email:jane@x.com"))


# --------------------------------------------------------------------------- #
# Determinism: same content -> same id (Req 4.6, 4.8)
# --------------------------------------------------------------------------- #
def test_same_content_yields_same_id() -> None:
    grp1 = [make_record("a", full_name="Jane Doe", email="Jane.Doe@Example.com")]
    grp2 = [make_record("a", full_name="Jane Doe", email="Jane.Doe@Example.com")]
    assert candidate_id_for_group(grp1) == candidate_id_for_group(grp2)


def test_email_case_and_whitespace_normalized_before_id() -> None:
    # Differently-cased / whitespace-padded emails normalize to the same key.
    a = candidate_id_for_group([make_record("a", email="  Jane.Doe@Example.com ")])
    b = candidate_id_for_group([make_record("b", email="jane.doe@example.com")])
    assert a == b


# --------------------------------------------------------------------------- #
# Idempotence across reordering (Req 4.8)
# --------------------------------------------------------------------------- #
def test_id_is_independent_of_record_order_within_group() -> None:
    r1 = make_record("a", full_name="Jane Doe", email="jane@x.com")
    r2 = make_record("b", full_name="Jane D", email="zoe@x.com", phone="+14155552671")
    forward = candidate_id_for_group([r1, r2])
    reversed_ = candidate_id_for_group([r2, r1])
    assert forward == reversed_
    # Smallest normalized email across the group is "jane@x.com".
    assert forward == str(uuid.uuid5(NAMESPACE_CANDIDATE, "email:jane@x.com"))


def test_assign_candidate_ids_preserves_group_order_and_pairs_ids() -> None:
    records = [
        make_record("a", full_name="Jane Doe", email="jane@x.com"),
        make_record("b", full_name="Bob Lee", phone="+14155552671"),
    ]
    groups = group(records)
    assigned = assign_candidate_ids(groups)
    assert len(assigned) == len(groups)
    for (cid, grp), original in zip(assigned, groups):
        assert grp is original
        assert cid == candidate_id_for_group(original)


# --------------------------------------------------------------------------- #
# Identity-key priority: email > phone > name (Req 4.7)
# --------------------------------------------------------------------------- #
def test_email_takes_priority_over_phone_and_name() -> None:
    grp = [make_record("a", full_name="Jane Doe", email="jane@x.com", phone="+14155552671")]
    cid = candidate_id_for_group(grp)
    assert cid == str(uuid.uuid5(NAMESPACE_CANDIDATE, "email:jane@x.com"))


def test_phone_takes_priority_over_name_when_no_email() -> None:
    grp = [make_record("a", full_name="Jane Doe", phone="+14155552671")]
    cid = candidate_id_for_group(grp)
    assert cid == str(uuid.uuid5(NAMESPACE_CANDIDATE, "phone:+14155552671"))


def test_name_used_when_no_email_or_phone() -> None:
    grp = [make_record("a", full_name="Jane Doe")]
    cid = candidate_id_for_group(grp)
    # Name key is lowercased and whitespace-collapsed.
    assert cid == str(uuid.uuid5(NAMESPACE_CANDIDATE, "name:jane doe"))


def test_smallest_email_chosen_across_multiple_records() -> None:
    grp = [
        make_record("a", email="zoe@x.com"),
        make_record("b", email="anna@x.com"),
        make_record("c", email="mike@x.com"),
    ]
    cid = candidate_id_for_group(grp)
    assert cid == str(uuid.uuid5(NAMESPACE_CANDIDATE, "email:anna@x.com"))


# --------------------------------------------------------------------------- #
# No-keys fallback (Req 4.7 all-empty case)
# --------------------------------------------------------------------------- #
def test_no_keys_fallback_is_deterministic() -> None:
    grp1 = [make_record("a", source_type="resume")]
    grp2 = [make_record("a", source_type="resume")]
    assert candidate_id_for_group(grp1) == candidate_id_for_group(grp2)
    expected = str(uuid.uuid5(NAMESPACE_CANDIDATE, "fallback:resume/a"))
    assert candidate_id_for_group(grp1) == expected


def test_no_keys_fallback_independent_of_record_order() -> None:
    r1 = make_record("a", source_type="resume")
    r2 = make_record("b", source_type="github")
    assert candidate_id_for_group([r1, r2]) == candidate_id_for_group([r2, r1])


def test_fallback_distinct_from_name_keyed_id() -> None:
    # An empty record and a name-bearing record must not collide.
    empty = candidate_id_for_group([make_record("a")])
    named = candidate_id_for_group([make_record("b", full_name="a")])
    assert empty != named

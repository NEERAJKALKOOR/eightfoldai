"""Property-based test for deterministic, idempotent candidate_id (Task 6.4).

Feature: candidate-data-transformer, Property 10

Property 10: candidate_id is deterministic and idempotent.
For any identity group, the derived candidate_id is a pure function of the
group's normalized identity key -- identical across repeated computation and
across input reorderings, and equal when the same source content is processed
again.

**Validates: Requirements 4.6, 4.7, 4.8**
"""

from __future__ import annotations

import uuid

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine.identity import candidate_id_for_group
from candidate_transformer.models import FieldValue, PerSourceRecord

# --------------------------------------------------------------------------- #
# Fixed pools so generated records have a real chance of sharing identity keys
# and so the same "content" can be reconstructed independently of object id.
# --------------------------------------------------------------------------- #
_EMAIL_POOL = [
    "jane.doe@example.com",
    "JANE.DOE@EXAMPLE.COM",
    "  bob@work.org ",
    "anna@x.com",
    "mike@x.com",
    "zoe@x.com",
]
_PHONE_POOL = [
    "+14155552671",
    "(415) 555-2671",
    "+442071838750",
    "+919876543210",
]
_NAME_POOL = [
    "Jane Doe",
    "  jane   doe ",
    "Bob Lee",
    "Anna Smith",
    "JANE DOE",
]
_SOURCE_TYPE_POOL = [
    "recruiter_csv",
    "ats_json",
    "resume",
    "linkedin",
    "github",
    "recruiter_notes",
]


# A blueprint is a plain, hashable description of a record's *content* (not the
# object itself). Rebuilding records from identical blueprints reproduces the
# same source content, which is what idempotence-across-reprocessing requires.
_record_blueprint = st.fixed_dictionaries(
    {
        "source_id": st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=6
        ),
        "source_type": st.sampled_from(_SOURCE_TYPE_POOL),
        "full_name": st.one_of(st.none(), st.sampled_from(_NAME_POOL)),
        "email": st.one_of(st.none(), st.sampled_from(_EMAIL_POOL)),
        "phone": st.one_of(st.none(), st.sampled_from(_PHONE_POOL)),
    }
)

# A group blueprint is a non-empty list of record blueprints.
_group_blueprint = st.lists(_record_blueprint, min_size=1, max_size=6)


def _build_record(blueprint: dict) -> PerSourceRecord:
    """Construct a fresh PerSourceRecord from a content blueprint (adapter shape)."""
    values: dict[str, FieldValue] = {}
    if blueprint["full_name"] is not None:
        values["full_name"] = FieldValue(value=blueprint["full_name"], method="test")
    if blueprint["email"] is not None:
        values["emails"] = FieldValue(value=blueprint["email"], method="test")
    if blueprint["phone"] is not None:
        values["phones"] = FieldValue(value=blueprint["phone"], method="test")
    return PerSourceRecord(
        source_id=blueprint["source_id"],
        source_type=blueprint["source_type"],
        values=values,
    )


def _build_group(blueprints: list[dict]) -> list[PerSourceRecord]:
    return [_build_record(bp) for bp in blueprints]


# --------------------------------------------------------------------------- #
# Property 10
# --------------------------------------------------------------------------- #
@given(blueprints=_group_blueprint, perm=st.randoms(use_true_random=False))
def test_candidate_id_is_deterministic_and_idempotent(blueprints, perm) -> None:
    """Property 10: candidate_id is a pure function of normalized identity content.

    Asserts, for any synthetic identity group:
    * Determinism -- repeated calls on the same group return the same id.
    * Order-independence -- shuffling the group's records does not change the id.
    * Idempotence across re-creation -- rebuilding records with identical content
      yields the same id.
    * The id is a valid UUID.

    **Validates: Requirements 4.6, 4.7, 4.8**
    """
    group_records = _build_group(blueprints)

    # 1. Determinism: repeated computation on the very same group is stable.
    cid = candidate_id_for_group(group_records)
    assert candidate_id_for_group(group_records) == cid

    # 2. Order-independence: a shuffled copy yields the same id.
    shuffled = list(group_records)
    perm.shuffle(shuffled)
    assert candidate_id_for_group(shuffled) == cid

    # 3. Idempotence across re-creation: rebuilding records with identical content
    #    (new objects, same source content) reproduces the same id.
    rebuilt = _build_group(blueprints)
    assert candidate_id_for_group(rebuilt) == cid

    # And the rebuilt group is likewise order-independent.
    rebuilt_shuffled = list(rebuilt)
    perm.shuffle(rebuilt_shuffled)
    assert candidate_id_for_group(rebuilt_shuffled) == cid

    # 4. The derived id is a valid UUID.
    parsed = uuid.UUID(cid)
    assert parsed.version == 5

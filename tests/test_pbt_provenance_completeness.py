"""Property-based test for provenance completeness and null explanation.

Feature: candidate-data-transformer, Property 13

Property 13: Provenance completeness and null explanation.
*For any* Canonical_Record, every field has at least one matching provenance
entry of shape ``{field, value, source, method, confidence}``; each contributing
value of a list-valued field has its own entry; and every field whose value is
null has a provenance entry with ``value = null`` and ``source``/``method``
indicating the value was not found.

This test exercises the Merge_Module's list-field combiner
(:func:`combine_list_field`), which is the unit responsible for emitting one
provenance entry per contributing list value (Req 6.4) and a single
``value=null`` "not found" entry for a field no source supplied (Req 6.3, 17.2).

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 17.1, 17.2**
"""

from __future__ import annotations

from collections import Counter

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine.merge import (
    LIST_VALUED_FIELDS,
    NOT_FOUND_METHOD,
    ListContribution,
    combine_list_field,
)
from candidate_transformer.models import ProvenanceEntry

# --- Smart generators constrained to the list-field input space ----------------

# The known source types whose reliability/priority the engine understands.
_SOURCE_TYPES = st.sampled_from(
    ["recruiter_csv", "ats_json", "resume", "linkedin", "github", "recruiter_notes"]
)

# List-valued canonical fields are homogeneous strings (emails, phones, skills,
# links.other). A small, repeat-prone alphabet makes duplicate contributions
# (two sources agreeing on a value) likely, exercising the per-value provenance rule.
_VALUES = st.sampled_from(
    ["python", "java", "go", "a@x.io", "b@y.io", "+14155550000", "+442071234567"]
)

_LIST_FIELDS = st.sampled_from(list(LIST_VALUED_FIELDS))


@st.composite
def list_contributions(draw: st.DrawFn) -> list[ListContribution]:
    """Generate a list of contributions for a single list-valued field.

    Values are drawn from a small alphabet so distinct sources frequently
    contribute the *same* value, ensuring the "one provenance entry per
    contributing value" rule (Req 6.4) is tested even when values dedupe.
    """
    n = draw(st.integers(min_value=1, max_value=8))
    contributions: list[ListContribution] = []
    for _ in range(n):
        contributions.append(
            ListContribution(
                value=draw(_VALUES),
                source_type=draw(_SOURCE_TYPES),
                source_id=draw(st.one_of(st.none(), st.text(min_size=1, max_size=12))),
                method=draw(st.one_of(st.none(), st.text(min_size=1, max_size=12))),
                field_confidence=draw(
                    st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
                ),
            )
        )
    return contributions


def _has_provenance_shape(entry: ProvenanceEntry) -> bool:
    """True if ``entry`` exposes the full {field, value, source, method, confidence} shape."""
    return all(
        hasattr(entry, attr)
        for attr in ("field", "value", "source", "method", "confidence")
    )


# --- Property 13 ---------------------------------------------------------------


@given(field_name=_LIST_FIELDS, contributions=list_contributions())
def test_every_contributing_value_has_its_own_provenance_entry(
    field_name: str, contributions: list[ListContribution]
) -> None:
    """One provenance entry per contributing list value, full shape (Req 6.1, 6.4)."""
    result = combine_list_field(field_name, contributions)

    # Req 6.4: one provenance entry per contributing value (including duplicates).
    assert len(result.provenance) == len(contributions)

    for entry in result.provenance:
        # Req 6.1: every entry has the canonical {field, value, source, method, confidence} shape.
        assert _has_provenance_shape(entry)
        # Req 6.2 / 17.1: the entry is attached to the field it explains.
        assert entry.field == field_name

    # Req 6.4: the multiset of provenance values matches the multiset of contributed values.
    assert Counter(str(e.value) for e in result.provenance) == Counter(
        str(c.value) for c in contributions
    )

    # Req 17.1: every provenance value traces back to an actually-contributed value.
    contributed = {c.value for c in contributions}
    for entry in result.provenance:
        assert entry.value in contributed


@given(field_name=_LIST_FIELDS)
def test_field_with_no_sources_gets_not_found_provenance(field_name: str) -> None:
    """A field no source supplied gets a single value=null "not found" entry (Req 6.3, 17.2)."""
    result = combine_list_field(field_name, [])

    # The deduped value list is empty when nothing contributed.
    assert result.values == []

    # Req 6.3: exactly one provenance entry explaining the absence.
    assert len(result.provenance) == 1
    entry = result.provenance[0]

    assert _has_provenance_shape(entry)
    assert entry.field == field_name
    # Req 6.3 / 17.2: value=null with source/method indicating "not found".
    assert entry.value is None
    assert entry.source is None
    assert entry.method == NOT_FOUND_METHOD
    assert entry.confidence == 0.0


@given(field_name=_LIST_FIELDS, contributions=list_contributions())
def test_provenance_covers_every_value_in_the_merged_list(
    field_name: str, contributions: list[ListContribution]
) -> None:
    """Every deduped value placed in the record is explained by some provenance entry (Req 6.2)."""
    result = combine_list_field(field_name, contributions)

    provenance_values = {e.value for e in result.provenance}
    for value in result.values:
        assert value in provenance_values

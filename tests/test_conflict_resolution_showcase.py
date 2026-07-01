"""Showcase test: conflicting values across sources -> correct winner + provenance.

This is the gold-profile edge case referenced in the design one-pager and the demo
video: the *same* candidate appears in two sources that **disagree** on several
single-valued fields, and the merge stage must resolve each conflict deterministically
and leave an auditable trail of where the winning value came from.

It exercises the Winner_Selection_Policy (Req 5.2-5.5), provenance for every field
(Req 6.1, 6.4), agreement-aware confidence (Req 7.4, 7.5), and list-value dedup that
preserves both lineages when two sources agree (Req 5.6, 6.4) -- all in one record.

Scenario
--------
* ``recruiter_csv`` (highest SourcePriority, reliability 0.95) and ``resume`` (lower
  priority, reliability 0.85) describe one person and **conflict** on ``full_name``
  and ``headline``.
* Both supply the *same* work email (they agree); the resume adds a second, personal
  email.

Expected resolution
-------------------
* Single-valued conflicts are won by the higher-priority source (recruiter CSV).
* Each winning field carries a provenance entry naming the source it came from.
* The shared email keeps a provenance entry for *each* contributing source, so the
  agreement is visible and not collapsed away.
"""

from __future__ import annotations

from candidate_transformer.engine.transformer import _build_canonical_record
from candidate_transformer.models import FieldValue, PerSourceRecord


def _record(source_type: str, source_id: str, values: dict) -> PerSourceRecord:
    return PerSourceRecord(source_id=source_id, source_type=source_type, values=values)


def _build_conflicting_group():
    """Two sources for one candidate that disagree on name and headline."""
    recruiter = _record(
        "recruiter_csv",
        "csv:1",
        {
            "full_name": FieldValue("Jane Anne Doe", "csv_column"),
            "headline": FieldValue("Senior Software Engineer", "csv_column"),
            "emails": FieldValue("jane@corp.com", "csv_column"),
        },
    )
    resume = _record(
        "resume",
        "resume:1",
        {
            "full_name": FieldValue("Jane A. Doe", "regex_name"),
            "headline": FieldValue("Software Engineer", "regex_headline"),
            # Same work email (agreement) + an extra personal email.
            "emails": FieldValue(["jane@corp.com", "jane.doe@personal.com"], "regex_email"),
        },
    )
    return [recruiter, resume]


def _provenance_for(record, field: str):
    return [p for p in record.provenance if p.field == field]


def test_higher_priority_source_wins_single_valued_conflicts():
    """SourcePriority breaks the tie: recruiter CSV beats resume on name + headline."""
    record = _build_canonical_record("cand-1", _build_conflicting_group())
    assert record.full_name == "Jane Anne Doe"
    assert record.headline == "Senior Software Engineer"


def test_winner_is_independent_of_input_order():
    """Reordering the sources does not change the resolved winners (Req 5.2)."""
    group = _build_conflicting_group()
    forward = _build_canonical_record("cand-1", group)
    reversed_ = _build_canonical_record("cand-1", list(reversed(group)))
    assert forward.full_name == reversed_.full_name == "Jane Anne Doe"
    assert forward.headline == reversed_.headline == "Senior Software Engineer"


def test_winning_field_records_its_source_in_provenance():
    """Every resolved field is traceable to the source it came from (Req 6.1)."""
    record = _build_canonical_record("cand-1", _build_conflicting_group())
    name_prov = _provenance_for(record, "full_name")
    assert len(name_prov) == 1
    assert name_prov[0].value == "Jane Anne Doe"
    assert name_prov[0].source == "csv:1"  # the recruiter CSV record
    assert name_prov[0].method == "csv_column"


def test_agreeing_email_keeps_both_source_lineages():
    """A value two sources agree on keeps a provenance entry per source (Req 6.4)."""
    record = _build_canonical_record("cand-1", _build_conflicting_group())
    # Combined + deduped + deterministically ordered.
    assert record.emails == ["jane.doe@personal.com", "jane@corp.com"]
    shared = [p for p in _provenance_for(record, "emails") if p.value == "jane@corp.com"]
    assert {p.source for p in shared} == {"csv:1", "resume:1"}


def test_agreement_raises_confidence_of_the_shared_value():
    """The agreed-upon email is at least as confident as the single-source one (Req 7.5)."""
    record = _build_canonical_record("cand-1", _build_conflicting_group())
    email_prov = {p.value: p for p in _provenance_for(record, "emails")}
    shared = max(
        p.confidence for p in _provenance_for(record, "emails") if p.value == "jane@corp.com"
    )
    solo = email_prov["jane.doe@personal.com"].confidence
    assert shared >= solo

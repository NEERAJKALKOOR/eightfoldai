"""Example unit tests for list-value dedup and provenance tracking (task 8.2).

Covers the reusable Merge_Module building blocks added in task 8.2:

* list dedup + deterministic ordering across sources (Req 5.6, 12.2),
* a provenance entry per contributing list value (Req 6.4),
* a deterministic provenance order (Req 12.3),
* a ``value=null`` "not found" provenance entry for fields no source provided
  (Req 6.3).

These are example tests; the matching property-based tests are tasks 8.4 and 8.5.
"""

from __future__ import annotations

from candidate_transformer.engine.merge import (
    NOT_FOUND_METHOD,
    ListContribution,
    combine_list_field,
    dedup_list_values,
    extract_list_items,
    list_contributions_for_field,
    not_found_provenance,
    order_provenance,
)
from candidate_transformer.models import FieldValue, Links, PerSourceRecord, ProvenanceEntry


# ---------------------------------------------------------------------------
# extract_list_items: flatten a per-source FieldValue into individual items
# ---------------------------------------------------------------------------


def test_extract_list_items_scalar_value():
    fv = FieldValue(value="jane@example.com", method="csv_column")
    assert extract_list_items("emails", fv) == ["jane@example.com"]


def test_extract_list_items_list_value():
    fv = FieldValue(value=["Python", "JavaScript"], method="pdf_section_skills")
    assert extract_list_items("skills", fv) == ["Python", "JavaScript"]


def test_extract_list_items_none_and_missing():
    assert extract_list_items("emails", None) == []
    assert extract_list_items("emails", FieldValue(value=None)) == []


def test_extract_list_items_drops_none_elements():
    fv = FieldValue(value=["Python", None, "Go"])
    assert extract_list_items("skills", fv) == ["Python", "Go"]


def test_extract_list_items_links_other():
    fv = FieldValue(
        value=Links(linkedin="https://linkedin.com/in/jane", other=["https://x.com/jane"]),
        method="regex_profile_links",
    )
    assert extract_list_items("links.other", fv) == ["https://x.com/jane"]


# ---------------------------------------------------------------------------
# dedup_list_values: dedup + deterministic ordering (Req 5.6, 12.2)
# ---------------------------------------------------------------------------


def test_dedup_removes_duplicates_and_sorts():
    assert dedup_list_values(["b", "a", "b", "c", "a"]) == ["a", "b", "c"]


def test_dedup_is_order_independent():
    first = dedup_list_values(["x@a.com", "y@b.com", "x@a.com"])
    second = dedup_list_values(["x@a.com", "x@a.com", "y@b.com"])
    third = dedup_list_values(["y@b.com", "x@a.com"])
    assert first == second == third == ["x@a.com", "y@b.com"]


# ---------------------------------------------------------------------------
# combine_list_field: dedup across sources + provenance per contribution
# ---------------------------------------------------------------------------


def test_combine_dedups_values_across_sources():
    contributions = [
        ListContribution("jane@example.com", "recruiter_csv", "csv:1", "csv_column", 0.9),
        ListContribution("jane@example.com", "resume", "resume:1", "regex_email", 0.8),
        ListContribution("j.doe@work.com", "ats_json", "ats:1", "json_field", 0.85),
    ]
    result = combine_list_field("emails", contributions)
    # Deduplicated, deterministically sorted (lexical on string form).
    assert result.values == ["j.doe@work.com", "jane@example.com"]


def test_combine_records_provenance_per_contributing_value():
    contributions = [
        ListContribution("jane@example.com", "recruiter_csv", "csv:1", "csv_column", 0.9),
        ListContribution("jane@example.com", "resume", "resume:1", "regex_email", 0.8),
        ListContribution("j.doe@work.com", "ats_json", "ats:1", "json_field", 0.85),
    ]
    result = combine_list_field("emails", contributions)
    # One provenance entry per contributing value (Req 6.4): 3 contributions -> 3.
    assert len(result.provenance) == 3
    assert all(isinstance(p, ProvenanceEntry) for p in result.provenance)
    assert all(p.field == "emails" for p in result.provenance)
    # The same value contributed by two sources keeps both lineages.
    jane_entries = [p for p in result.provenance if p.value == "jane@example.com"]
    assert {p.source for p in jane_entries} == {"csv:1", "resume:1"}
    assert {p.method for p in jane_entries} == {"csv_column", "regex_email"}


def test_combine_provenance_is_deterministically_ordered():
    a = ListContribution("jane@example.com", "recruiter_csv", "csv:1", "csv_column", 0.9)
    b = ListContribution("jane@example.com", "resume", "resume:1", "regex_email", 0.8)
    c = ListContribution("j.doe@work.com", "ats_json", "ats:1", "json_field", 0.85)

    forward = combine_list_field("emails", [a, b, c]).provenance
    shuffled = combine_list_field("emails", [c, b, a]).provenance

    key = lambda p: (str(p.value), p.source, p.method)
    assert [key(p) for p in forward] == [key(p) for p in shuffled]


def test_combine_empty_yields_not_found_entry():
    result = combine_list_field("phones", [])
    assert result.values == []
    assert len(result.provenance) == 1
    entry = result.provenance[0]
    assert entry.field == "phones"
    assert entry.value is None
    assert entry.source is None
    assert entry.method == NOT_FOUND_METHOD
    assert entry.confidence == 0.0


# ---------------------------------------------------------------------------
# not_found_provenance / order_provenance helpers
# ---------------------------------------------------------------------------


def test_not_found_provenance_shape():
    entry = not_found_provenance("skills")
    assert entry == ProvenanceEntry(
        field="skills", value=None, source=None, method=NOT_FOUND_METHOD, confidence=0.0
    )


def test_order_provenance_is_stable_and_total():
    entries = [
        ProvenanceEntry("emails", "b@x.com", "s2", "m", 0.5),
        ProvenanceEntry("emails", "a@x.com", "s1", "m", 0.5),
        ProvenanceEntry("emails", "a@x.com", "s0", "m", 0.5),
    ]
    ordered = order_provenance(entries)
    assert [(p.value, p.source) for p in ordered] == [
        ("a@x.com", "s0"),
        ("a@x.com", "s1"),
        ("b@x.com", "s2"),
    ]


# ---------------------------------------------------------------------------
# list_contributions_for_field: building contributions from PerSourceRecords
# ---------------------------------------------------------------------------


def _record(source_type: str, source_id: str, values: dict) -> PerSourceRecord:
    return PerSourceRecord(source_id=source_id, source_type=source_type, values=values)


def test_list_contributions_for_field_flattens_records():
    records = [
        _record("recruiter_csv", "csv:1", {"emails": FieldValue("jane@example.com", "csv_column")}),
        _record("resume", "resume:1", {"emails": FieldValue("j.doe@work.com", "regex_email")}),
    ]
    contributions = list_contributions_for_field("emails", records)
    assert [c.value for c in contributions] == ["jane@example.com", "j.doe@work.com"]
    assert [c.source_id for c in contributions] == ["csv:1", "resume:1"]
    assert [c.method for c in contributions] == ["csv_column", "regex_email"]


def test_list_contributions_for_skills_list_field():
    records = [
        _record("resume", "resume:1", {"skills": FieldValue(["Python", "Go"], "pdf_section_skills")}),
        _record("github", "gh:1", {"skills": FieldValue(["Python"], "repo_languages")}),
    ]
    contributions = list_contributions_for_field("skills", records)
    result = combine_list_field("skills", contributions)
    assert result.values == ["Go", "Python"]
    # 3 contributing values (Python x2, Go x1) -> 3 provenance entries.
    assert len(result.provenance) == 3


def test_list_contributions_for_links_other():
    records = [
        _record(
            "resume",
            "resume:1",
            {"links": FieldValue(Links(other=["https://x.com/jane"]), "regex_profile_links")},
        ),
        _record("recruiter_notes", "notes:1", {"links": FieldValue(None, "regex_profile_links")}),
    ]
    contributions = list_contributions_for_field("links.other", records)
    result = combine_list_field("links.other", contributions)
    assert result.values == ["https://x.com/jane"]
    assert len(result.provenance) == 1
    assert result.provenance[0].field == "links.other"
    assert result.provenance[0].source == "resume:1"


def test_list_contributions_confidence_callback():
    records = [
        _record("recruiter_csv", "csv:1", {"emails": FieldValue("jane@example.com", "csv_column")}),
    ]
    contributions = list_contributions_for_field(
        "emails", records, confidence_for=lambda field, rec, item: 0.77
    )
    assert contributions[0].field_confidence == 0.77

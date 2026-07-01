"""Unit tests for the core data models (Task 2.1).

Verifies construction, the all-null factories (Req 10.4), the documented shapes
of Error_Report (Req 11.1) and Log_Entry (Req 11.3), and deterministic,
JSON-ready serialization.
"""

from __future__ import annotations

import json

from candidate_transformer.models import (
    CanonicalRecord,
    Canonical_Record,
    EducationEntry,
    ErrorReport,
    ExperienceEntry,
    FieldValue,
    Links,
    Location,
    LogEntry,
    PerSourceRecord,
    ProvenanceEntry,
    RunResult,
    Skill,
    canonical_record_to_dict,
    new_null_canonical_record,
    new_null_per_source_record,
    run_result_to_dict,
    to_dict,
)


def test_canonical_record_spec_alias_is_same_class():
    assert Canonical_Record is CanonicalRecord


def test_new_null_canonical_record_is_all_null():
    record = new_null_canonical_record()

    # Scalars are None.
    assert record.candidate_id is None
    assert record.full_name is None
    assert record.headline is None
    assert record.years_experience is None
    # Lists are empty.
    assert record.emails == []
    assert record.phones == []
    assert record.skills == []
    assert record.experience == []
    assert record.education == []
    assert record.provenance == []
    # Nested structures are all-null.
    assert record.location == Location(city=None, region=None, country=None)
    assert record.links == Links(linkedin=None, github=None, portfolio=None, other=[])
    # Overall confidence is 0.0.
    assert record.overall_confidence == 0.0


def test_null_records_do_not_share_mutable_state():
    a = new_null_canonical_record()
    b = new_null_canonical_record()
    a.emails.append("x@example.com")
    a.links.other.append("https://example.com")
    assert b.emails == []
    assert b.links.other == []


def test_canonical_record_empty_accepts_candidate_id():
    record = CanonicalRecord.empty(candidate_id="abc")
    assert record.candidate_id == "abc"
    assert record.overall_confidence == 0.0


def test_full_canonical_record_construction_and_serialization():
    record = CanonicalRecord(
        candidate_id="cid-1",
        full_name="Jane Doe",
        emails=["jane@example.com"],
        phones=["+14155552671"],
        location=Location(city="Seattle", region="WA", country="US"),
        links=Links(linkedin="https://linkedin.com/in/jane", other=["https://x.com/jane"]),
        headline="Engineer",
        years_experience=7.0,
        skills=[Skill(name="Python", confidence=0.9, sources=["src-1"])],
        experience=[ExperienceEntry(company="Acme", title="Dev", start="2020-01", end="2022-03")],
        education=[EducationEntry(institution="MIT", degree="BS", field="CS", end_year=2015)],
        provenance=[ProvenanceEntry(field="full_name", value="Jane Doe", source="src-1", method="csv_column", confidence=0.9)],
        overall_confidence=0.85,
    )

    as_dict = canonical_record_to_dict(record)
    # JSON-ready: round-trips through json without error.
    text = json.dumps(as_dict)
    assert json.loads(text)["candidate_id"] == "cid-1"
    # Nested dataclasses become plain dicts.
    assert as_dict["location"] == {"city": "Seattle", "region": "WA", "country": "US"}
    assert as_dict["skills"][0] == {"name": "Python", "confidence": 0.9, "sources": ["src-1"]}


def test_serialization_is_deterministic():
    record = new_null_canonical_record(candidate_id="cid")
    first = json.dumps(canonical_record_to_dict(record))
    second = json.dumps(canonical_record_to_dict(record))
    assert first == second
    # Field declaration order is stable / deterministic.
    assert list(canonical_record_to_dict(record).keys())[0] == "candidate_id"


def test_provenance_entry_null_explanation_shape():
    entry = ProvenanceEntry(field="phones", value=None, source=None, method=None, confidence=0.0)
    assert to_dict(entry) == {
        "field": "phones",
        "value": None,
        "source": None,
        "method": None,
        "confidence": 0.0,
    }


def test_field_value_defaults():
    fv = FieldValue()
    assert fv.value is None
    assert fv.method is None
    assert fv.normalization_quality == 0.0


def test_per_source_record_construction_and_null_factory():
    record = PerSourceRecord(
        source_id="src-1",
        source_type="recruiter_csv",
        values={"full_name": FieldValue(value="Jane", method="csv_column", normalization_quality=1.0)},
    )
    assert record.values["full_name"].value == "Jane"
    assert record.errors == []

    null_record = new_null_per_source_record("src-2", "ats_json")
    assert null_record.values == {}
    assert null_record.errors == []

    # asdict recurses into the FieldValue values of the mapping.
    assert to_dict(record)["values"]["full_name"]["normalization_quality"] == 1.0


def test_error_report_shape():
    err = ErrorReport(source="src-1", stage="ingest", error="file missing")
    assert to_dict(err) == {"source": "src-1", "stage": "ingest", "error": "file missing"}


def test_log_entry_shape():
    entry = LogEntry(timestamp="2024-01-01T00:00:00Z", level="INFO", module="ingest", message="loaded")
    assert to_dict(entry) == {
        "timestamp": "2024-01-01T00:00:00Z",
        "level": "INFO",
        "module": "ingest",
        "message": "loaded",
    }


def test_run_result_default_is_clean_and_serializable():
    result = RunResult.empty()
    assert result.profiles == []
    assert result.errors == []
    assert result.exit_code == 0

    result.errors.append(ErrorReport(source=None, stage="merge", error="all sources failed"))
    result.profiles.append({"id": "cid-1"})
    result.exit_code = 1

    as_dict = run_result_to_dict(result)
    assert json.loads(json.dumps(as_dict)) == {
        "profiles": [{"id": "cid-1"}],
        "errors": [{"source": None, "stage": "merge", "error": "all sources failed"}],
        "exit_code": 1,
    }

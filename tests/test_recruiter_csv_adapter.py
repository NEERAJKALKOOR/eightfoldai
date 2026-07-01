"""Unit tests for the Recruiter CSV adapter (Task 5.2).

Covers can_handle recognition, ingest read-failure -> IngestError, and faithful
extraction of the sample fixture (Jane Doe, John Smith, Bob Lee) including the
null-honesty rule for Bob Lee's blank company and unparseable phone (captured
verbatim, normalization deferred to a later stage).

_Requirements: 1.3, 2.1, 2.2, 2.4, 2.6, 1.9_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import (
    AdapterRegistry,
    RecruiterCsvAdapter,
    build_default_registry,
)
from candidate_transformer.adapters.base import (
    IngestError,
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import ExperienceEntry, PerSourceRecord

SAMPLE_CSV = (
    Path(__file__).resolve().parent.parent / "samples" / "recruiter.csv"
)


@pytest.fixture()
def adapter() -> RecruiterCsvAdapter:
    return RecruiterCsvAdapter()


# -- attributes ------------------------------------------------------------


def test_adapter_attributes_come_from_shared_tables(adapter: RecruiterCsvAdapter) -> None:
    assert adapter.source_type == "recruiter_csv"
    assert adapter.reliability == reliability_of("recruiter_csv") == 0.95
    assert adapter.priority == priority_of("recruiter_csv") == 0


# -- can_handle ------------------------------------------------------------


def test_can_handle_by_explicit_source_type(adapter: RecruiterCsvAdapter) -> None:
    ref = SourceRef(location="whatever.txt", source_type="recruiter_csv")
    assert adapter.can_handle(ref) is True


def test_can_handle_by_csv_extension(adapter: RecruiterCsvAdapter) -> None:
    assert adapter.can_handle(SourceRef(location="/data/recruiter.CSV")) is True
    assert adapter.can_handle(SourceRef(location="recruiter.csv")) is True


def test_can_handle_rejects_unrelated_reference(adapter: RecruiterCsvAdapter) -> None:
    assert adapter.can_handle(SourceRef(location="resume.pdf")) is False
    assert adapter.can_handle(SourceRef(location="https://github.com/x")) is False


# -- ingest ----------------------------------------------------------------


def test_ingest_reads_file_text_and_sets_provenance(adapter: RecruiterCsvAdapter) -> None:
    raw = adapter.ingest(SourceRef(location=str(SAMPLE_CSV)))
    assert raw.source_id == str(SAMPLE_CSV)
    assert raw.source_type == "recruiter_csv"
    assert "Jane Doe" in raw.content


def test_ingest_missing_file_raises_ingest_error(adapter: RecruiterCsvAdapter) -> None:
    with pytest.raises(IngestError):
        adapter.ingest(SourceRef(location="does-not-exist-12345.csv"))


# -- extract: sample fixture ----------------------------------------------


def _extract_sample(adapter: RecruiterCsvAdapter) -> list[PerSourceRecord]:
    raw = adapter.ingest(SourceRef(location=str(SAMPLE_CSV)))
    return adapter.extract(raw)


def test_extract_one_record_per_row(adapter: RecruiterCsvAdapter) -> None:
    records = _extract_sample(adapter)
    assert len(records) == 3
    assert [r.values["full_name"].value for r in records] == [
        "Jane Doe",
        "John Smith",
        "Bob Lee",
    ]


def test_extract_provenance_copied_onto_every_record(adapter: RecruiterCsvAdapter) -> None:
    records = _extract_sample(adapter)
    for rec in records:
        assert rec.source_type == "recruiter_csv"
        assert rec.source_id == str(SAMPLE_CSV)


def test_extract_records_method_on_every_value(adapter: RecruiterCsvAdapter) -> None:
    rec = _extract_sample(adapter)[0]
    for fv in rec.values.values():
        assert fv.method == "csv_column"


def test_jane_doe_mapped_faithfully(adapter: RecruiterCsvAdapter) -> None:
    jane = _extract_sample(adapter)[0]
    # email case preserved (no lowercasing) -- normalization is a later stage.
    assert jane.values["emails"].value == "Jane.Doe@example.com"
    assert jane.values["phones"].value == "(415) 555-2671"
    assert jane.values["headline"].value == "Senior Engineer"
    exp = jane.values["experience"].value
    assert exp == [ExperienceEntry(company="Acme Corp", title="Senior Engineer")]


def test_bob_lee_blank_company_is_null_and_phone_captured_verbatim(
    adapter: RecruiterCsvAdapter,
) -> None:
    bob = _extract_sample(adapter)[2]
    # Unparseable phone captured verbatim; normalization deferred (Req 2.6).
    assert bob.values["phones"].value == "not-a-phone"
    # Blank company -> the experience entry has company=None but keeps the title.
    exp = bob.values["experience"].value
    assert exp == [ExperienceEntry(company=None, title="Developer")]
    assert bob.values["headline"].value == "Developer"


# -- extract: edge cases ---------------------------------------------------


def test_empty_file_yields_no_records(adapter: RecruiterCsvAdapter) -> None:
    raw = RawSource(source_id="empty.csv", source_type="recruiter_csv", content="")
    assert adapter.extract(raw) == []


def test_header_only_file_yields_no_records(adapter: RecruiterCsvAdapter) -> None:
    raw = RawSource(
        source_id="h.csv",
        source_type="recruiter_csv",
        content="name,email,phone,current_company,title\n",
    )
    assert adapter.extract(raw) == []


def test_row_with_all_blank_columns_yields_all_null_values(
    adapter: RecruiterCsvAdapter,
) -> None:
    content = "name,email,phone,current_company,title\n,,,,\n"
    raw = RawSource(source_id="b.csv", source_type="recruiter_csv", content=content)
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value is None
    assert rec.values["emails"].value is None
    assert rec.values["phones"].value is None
    assert rec.values["headline"].value is None
    # Both company and title blank -> experience is a single null value (not invented).
    assert rec.values["experience"].value is None


def test_missing_and_extra_columns_tolerated(adapter: RecruiterCsvAdapter) -> None:
    # Header omits current_company/title and adds an unrelated extra column.
    content = "name,email,phone,extra\nAmy,amy@x.com,123,junk\n"
    raw = RawSource(source_id="m.csv", source_type="recruiter_csv", content=content)
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value == "Amy"
    assert rec.values["emails"].value == "amy@x.com"
    # Absent columns -> null FieldValue, never invented (Req 2.4).
    assert rec.values["headline"].value is None
    assert rec.values["experience"].value is None


def test_whitespace_is_trimmed_but_value_otherwise_preserved(
    adapter: RecruiterCsvAdapter,
) -> None:
    content = "name,email,phone,current_company,title\n  Jane  , X@Y.com ,, ,Eng \n"
    raw = RawSource(source_id="w.csv", source_type="recruiter_csv", content=content)
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value == "Jane"
    assert rec.values["emails"].value == "X@Y.com"  # case preserved
    assert rec.values["phones"].value is None  # blank -> null


# -- registry integration --------------------------------------------------


def test_default_registry_resolves_csv_reference() -> None:
    registry = build_default_registry()
    adapter = registry.resolve(SourceRef(location="recruiter.csv"))
    assert isinstance(adapter, RecruiterCsvAdapter)


def test_adapter_satisfies_registry_protocol() -> None:
    registry = AdapterRegistry([RecruiterCsvAdapter()])
    assert registry.adapters[0].source_type == "recruiter_csv"

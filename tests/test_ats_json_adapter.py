"""Example unit tests for the ATS JSON adapter (Task 5.6).

A known ATS JSON fixture yields the expected ``PerSourceRecord`` list with the
correct ``source_type``/``source_id`` (Req 1.9) and ATS-key -> canonical-field
mapping applied through the declarative field-mapping table (Req 1.4, 2.3). Each
value records the extraction method identifying its originating ATS key (Req 2.5),
and absent keys are never invented (Req 2.4, 2.6).

_Requirements: 1.4, 1.9, 2.3, 2.5_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import (
    AtsJsonAdapter,
    build_default_registry,
)
from candidate_transformer.adapters.base import (
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import ExperienceEntry, PerSourceRecord

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ats.json"


@pytest.fixture()
def adapter() -> AtsJsonAdapter:
    return AtsJsonAdapter()


def _extract(adapter: AtsJsonAdapter) -> list[PerSourceRecord]:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE)))
    return adapter.extract(raw)


# -- attributes / provenance anchors --------------------------------------


def test_adapter_attributes_come_from_shared_tables(adapter: AtsJsonAdapter) -> None:
    assert adapter.source_type == "ats_json"
    assert adapter.reliability == reliability_of("ats_json") == 0.90
    assert adapter.priority == priority_of("ats_json") == 1


def test_ingest_sets_source_provenance(adapter: AtsJsonAdapter) -> None:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE)))
    assert raw.source_id == str(FIXTURE)
    assert raw.source_type == "ats_json"


# -- one record per array element with indexed source_id (Req 1.9) --------


def test_array_yields_one_record_per_element_with_indexed_ids(
    adapter: AtsJsonAdapter,
) -> None:
    records = _extract(adapter)
    assert len(records) == 3
    # Each record is tagged ats_json and carries an index-suffixed source_id.
    for index, rec in enumerate(records):
        assert rec.source_type == "ats_json"
        assert rec.source_id == f"{FIXTURE}#{index}"


# -- ATS-key -> canonical-field mapping (Req 1.4, 2.3) --------------------


def test_jane_doe_keys_mapped_to_canonical_fields(adapter: AtsJsonAdapter) -> None:
    jane = _extract(adapter)[0]
    # candidateName -> full_name, emailAddress -> emails, phoneNumber -> phones.
    assert jane.values["full_name"].value == "Jane Doe"
    assert jane.values["emails"].value == "jane.doe@example.com"
    assert jane.values["phones"].value == "+1 (415) 555-2671"
    # jobTitle -> headline (and experience[].title); yrsExp -> years_experience.
    assert jane.values["headline"].value == "Staff Engineer"
    assert jane.values["years_experience"].value == 9
    # skillList -> skills[] captured verbatim (normalization is a later stage).
    assert jane.values["skills"].value == ["py", "JS", "Kubernetes"]
    # currentEmployer + jobTitle -> a single experience entry.
    assert jane.values["experience"].value == [
        ExperienceEntry(company="Acme Corporation", title="Staff Engineer")
    ]


def test_extraction_method_records_originating_ats_key(adapter: AtsJsonAdapter) -> None:
    jane = _extract(adapter)[0]
    assert jane.values["full_name"].method == "ats_field:candidateName"
    assert jane.values["emails"].method == "ats_field:emailAddress"
    assert jane.values["phones"].method == "ats_field:phoneNumber"
    assert jane.values["skills"].method == "ats_field:skillList"
    # The experience entry combines the keys that contributed it.
    assert jane.values["experience"].method == "ats_field:currentEmployer+jobTitle"


def test_absent_key_is_not_invented(adapter: AtsJsonAdapter) -> None:
    # The fixture has no education-related ATS key, so no education value appears.
    jane = _extract(adapter)[0]
    assert "education" not in jane.values


# -- registry routing ------------------------------------------------------


def test_default_registry_resolves_json_reference() -> None:
    registry = build_default_registry()
    resolved = registry.resolve(SourceRef(location="ats.json"))
    assert isinstance(resolved, AtsJsonAdapter)


# -- location assembly from city/region/state/country (Req 2.3) -----------


def _extract_object(adapter: AtsJsonAdapter, payload: dict) -> PerSourceRecord:
    """Extract a single record from an in-memory ATS object (no fixture file)."""
    import json as _json

    from candidate_transformer.adapters.base import RawSource
    from candidate_transformer.models import Location  # noqa: F401 (import check)

    ref = SourceRef(location="mem", source_type="ats_json")
    raw = RawSource(
        source_id="mem", source_type="ats_json", content=_json.dumps(payload), ref=ref
    )
    return adapter.extract(raw)[0]


def test_location_assembled_from_city_state_country(adapter: AtsJsonAdapter) -> None:
    from candidate_transformer.models import Location

    record = _extract_object(
        adapter,
        {
            "candidateName": "Neeraj Kalkoor",
            "city": "Bangalore",
            "state": "Karnataka",
            "country": "India",
        },
    )
    location = record.values["location"].value
    assert isinstance(location, Location)
    assert location.city == "Bangalore"
    # "state" maps to the canonical region subfield.
    assert location.region == "Karnataka"
    # Country is recorded verbatim; ISO-3166 normalization happens downstream.
    assert location.country == "India"
    # Combined provenance names the contributing ATS keys.
    assert record.values["location"].method.startswith("ats_field:")


def test_location_city_only(adapter: AtsJsonAdapter) -> None:
    from candidate_transformer.models import Location

    record = _extract_object(adapter, {"candidateName": "N K", "city": "Bangalore"})
    location = record.values["location"].value
    assert isinstance(location, Location)
    assert location.city == "Bangalore"
    assert location.region is None
    assert location.country is None


def test_no_location_keys_yields_no_location_value(adapter: AtsJsonAdapter) -> None:
    # None of the fixture records carry city/region/country -> no location value.
    record = _extract_object(adapter, {"candidateName": "N K", "emailAddress": "n@x.com"})
    assert "location" not in record.values

"""Example unit tests for the LinkedIn profile adapter (Task 5.6).

A known LinkedIn profile payload fixture yields a single ``PerSourceRecord`` with
the correct ``source_type``/``source_id`` (Req 1.9) and the documented mapping:
name -> full_name, headline, location parsed, skills, experience, education, and
the profile link -> links.linkedin (Req 1.6). Each value records its extraction
method for provenance (Req 2.5); absent fields are recorded null, never invented
(Req 2.4, 2.6).

_Requirements: 1.6, 1.9, 2.5_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import LinkedinAdapter, build_default_registry
from candidate_transformer.adapters.base import (
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import (
    EducationEntry,
    ExperienceEntry,
    Links,
    Location,
    PerSourceRecord,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "linkedin_jane.json"


@pytest.fixture()
def adapter() -> LinkedinAdapter:
    return LinkedinAdapter()


def _extract(adapter: LinkedinAdapter) -> PerSourceRecord:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE), source_type="linkedin"))
    records = adapter.extract(raw)
    assert len(records) == 1
    return records[0]


# -- attributes / provenance anchors --------------------------------------


def test_adapter_attributes_come_from_shared_tables(adapter: LinkedinAdapter) -> None:
    assert adapter.source_type == "linkedin"
    assert adapter.reliability == reliability_of("linkedin") == 0.80
    assert adapter.priority == priority_of("linkedin") == 3


def test_source_provenance_recorded(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    assert rec.source_type == "linkedin"
    assert rec.source_id == str(FIXTURE)


# -- documented field mapping (Req 1.6) -----------------------------------


def test_scalar_fields_mapped(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["full_name"].value == "Jane Doe"
    assert rec.values["headline"].value == (
        "Senior Software Engineer at Acme Corporation"
    )
    assert rec.values["emails"].value == "jane.doe@example.com"
    assert rec.values["location"].value == Location(
        city="San Francisco", region="California", country="United States"
    )


def test_skills_deduped_verbatim(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    # Duplicate "Python" collapsed, first-seen order preserved; raw (un-normalized).
    assert rec.values["skills"].value == ["Python", "JavaScript", "Kubernetes"]


def test_experience_entries_in_order_with_verbatim_dates(
    adapter: LinkedinAdapter,
) -> None:
    rec = _extract(adapter)
    assert rec.values["experience"].value == [
        ExperienceEntry(
            company="Acme Corporation",
            title="Senior Software Engineer",
            start="March 2019",
            end="Present",
            summary="Led migration to a service-oriented architecture.",
        ),
        ExperienceEntry(
            company="Globex Systems",
            title="Software Engineer",
            start="June 2015",
            end="February 2019",
        ),
    ]


def test_education_entry_with_coerced_year(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["education"].value == [
        EducationEntry(
            institution="University of California, Berkeley",
            degree="B.S.",
            field="Computer Science",
            end_year=2015,
        )
    ]


def test_profile_link_mapped_to_links_linkedin(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["links"].value == Links(
        linkedin="https://www.linkedin.com/in/janedoe"
    )


def test_extraction_methods_recorded(adapter: LinkedinAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["full_name"].method == "linkedin_profile_name"
    assert rec.values["headline"].method == "linkedin_profile_headline"
    assert rec.values["skills"].method == "linkedin_profile_skills"
    assert rec.values["experience"].method == "linkedin_profile_experience"
    assert rec.values["education"].method == "linkedin_profile_education"
    assert rec.values["links"].method == "linkedin_profile_url"


# -- null-honesty (Req 2.4, 2.6) ------------------------------------------


def test_missing_fields_are_null_not_invented() -> None:
    from candidate_transformer.adapters.base import RawSource

    adapter = LinkedinAdapter()
    raw = RawSource(source_id="x", source_type="linkedin", content='{"name": "Solo"}')
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value == "Solo"
    assert rec.values["headline"].value is None
    assert rec.values["skills"].value is None
    assert rec.values["experience"].value is None
    assert rec.values["education"].value is None
    assert rec.values["links"].value is None


# -- registry routing ------------------------------------------------------


def test_default_registry_resolves_linkedin_url() -> None:
    registry = build_default_registry()
    resolved = registry.resolve(
        SourceRef(location="https://www.linkedin.com/in/janedoe")
    )
    assert isinstance(resolved, LinkedinAdapter)

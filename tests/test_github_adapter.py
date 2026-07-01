"""Example unit tests for the GitHub profile adapter (Task 5.6).

A known GitHub profile payload fixture yields a single ``PerSourceRecord`` with
the correct ``source_type``/``source_id`` (Req 1.9) and the documented mapping:
name -> full_name, bio -> headline, location parsed, repo languages -> skills, and
the profile link -> links.github (Req 1.5). Each value records its extraction
method for provenance (Req 2.5); absent fields are recorded null, never invented
(Req 2.4, 2.6).

_Requirements: 1.5, 1.9, 2.5_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import GithubAdapter, build_default_registry
from candidate_transformer.adapters.base import (
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import Links, Location, PerSourceRecord

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "github_jane.json"


@pytest.fixture()
def adapter() -> GithubAdapter:
    return GithubAdapter()


def _extract(adapter: GithubAdapter) -> PerSourceRecord:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE), source_type="github"))
    records = adapter.extract(raw)
    assert len(records) == 1  # a profile describes exactly one candidate
    return records[0]


# -- attributes / provenance anchors --------------------------------------


def test_adapter_attributes_come_from_shared_tables(adapter: GithubAdapter) -> None:
    assert adapter.source_type == "github"
    assert adapter.reliability == reliability_of("github") == 0.70
    assert adapter.priority == priority_of("github") == 4


def test_source_provenance_recorded(adapter: GithubAdapter) -> None:
    rec = _extract(adapter)
    assert rec.source_type == "github"
    assert rec.source_id == str(FIXTURE)


# -- documented field mapping (Req 1.5) -----------------------------------


def test_profile_fields_mapped(adapter: GithubAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["full_name"].value == "Jane Doe"
    assert rec.values["headline"].value == (
        "Senior Software Engineer building distributed systems"
    )
    assert rec.values["emails"].value == "jane.doe@example.com"
    assert rec.values["location"].value == Location(
        city="San Francisco", region="CA", country="USA"
    )


def test_repo_languages_become_deduped_skills(adapter: GithubAdapter) -> None:
    rec = _extract(adapter)
    # Flat languages then repo languages, de-duplicated, first-seen order kept.
    assert rec.values["skills"].value == ["Python", "Go", "HCL"]


def test_profile_link_mapped_to_links_github(adapter: GithubAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["links"].value == Links(github="https://github.com/janedoe")


def test_extraction_methods_recorded(adapter: GithubAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["full_name"].method == "github_profile_name"
    assert rec.values["headline"].method == "github_profile_bio"
    assert rec.values["location"].method == "github_profile_location"
    assert rec.values["skills"].method == "github_repo_languages"
    assert rec.values["links"].method == "github_profile_url"


# -- null-honesty (Req 2.4, 2.6) ------------------------------------------


def test_missing_fields_are_null_not_invented() -> None:
    adapter = GithubAdapter()
    from candidate_transformer.adapters.base import RawSource

    raw = RawSource(source_id="x", source_type="github", content='{"login": "ghost"}')
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value is None
    assert rec.values["headline"].value is None
    assert rec.values["location"].value is None
    assert rec.values["skills"].value is None
    # A login still derives the canonical profile URL deterministically.
    assert rec.values["links"].value == Links(github="https://github.com/ghost")


# -- registry routing ------------------------------------------------------


def test_default_registry_resolves_github_url() -> None:
    registry = build_default_registry()
    resolved = registry.resolve(SourceRef(location="https://github.com/janedoe"))
    assert isinstance(resolved, GithubAdapter)

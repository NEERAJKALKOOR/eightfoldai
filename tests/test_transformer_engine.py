"""Example/integration tests for the TransformerEngine orchestration (task 14.1).

These are concrete end-to-end tests (not the property-based tests, which are the
separate tasks 14.2-14.4). They exercise:

* a clean multi-source run over the sample fixtures with the default projection
  config -- producing one profile per identity group and merging the overlapping
  candidate (Jane Doe) who appears in all four sources (Req 14.1, 1.2);
* graceful degradation when a referenced source is missing -- a structured
  ``ingest`` Error_Report is recorded and the run continues (Req 10.1, 10.5);
* determinism -- two runs over the same inputs produce identical profiles
  (Req 12.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine import ProjectionConfig, TransformerEngine
from candidate_transformer.models import RunResult

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLES = _REPO_ROOT / "samples"
_DEFAULT_CONFIG_PATH = _SAMPLES / "configs" / "default.json"


def _default_config() -> dict:
    """Load the default projection config from the samples directory."""
    return json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _sample_refs() -> list[SourceRef]:
    """The four overlapping sample sources, routed to their adapters."""
    return [
        SourceRef(location=str(_SAMPLES / "recruiter.csv")),
        SourceRef(location=str(_SAMPLES / "ats.json")),
        SourceRef(location=str(_SAMPLES / "resume_jane_doe.docx")),
        SourceRef(location=str(_SAMPLES / "notes.txt")),
    ]


@pytest.fixture()
def config() -> ProjectionConfig:
    return ProjectionConfig.from_dict(_default_config())


# --------------------------------------------------------------------------- #
# Clean multi-source run
# --------------------------------------------------------------------------- #
def test_clean_multi_source_run_produces_profiles(config: ProjectionConfig) -> None:
    """A clean run over all four sample sources yields profiles with no errors."""
    engine = TransformerEngine()
    result = engine.run(_sample_refs(), config)

    assert isinstance(result, RunResult)
    assert result.profiles, "expected at least one projected profile"
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert result.exit_code == 0

    # Every profile carries the required, renamed output fields from the config.
    for profile in result.profiles:
        assert "id" in profile and profile["id"]
        assert "name" in profile  # required, on_missing=null -> present (maybe null)
        assert "overall_confidence" in profile  # include_confidence is true


def test_overlapping_candidate_is_merged(config: ProjectionConfig) -> None:
    """Jane Doe appears in all four sources but yields exactly one merged profile."""
    result = TransformerEngine().run(_sample_refs(), config)

    jane_profiles = [
        p for p in result.profiles if p.get("primary_email") == "jane.doe@example.com"
    ]
    assert len(jane_profiles) == 1, "Jane Doe's sources should merge into one profile"

    jane = jane_profiles[0]
    assert jane["name"] == "Jane Doe"
    # Skills from multiple sources are merged + canonicalized + deduplicated.
    assert "Python" in jane["skills"]
    assert "JavaScript" in jane["skills"]
    assert "Kubernetes" in jane["skills"]
    # No duplicates in the merged skill list.
    assert len(jane["skills"]) == len(set(jane["skills"]))
    # Jane's resume supplies a US location, which is in the config enum.
    assert jane.get("country") == "US"


def test_near_duplicate_names_are_not_over_merged(config: ProjectionConfig) -> None:
    """John Smith and Jon Smyth share no contact info, so stay separate (no over-merge)."""
    result = TransformerEngine().run(_sample_refs(), config)
    emails = {p.get("primary_email") for p in result.profiles}
    assert "john.smith@example.com" in emails
    assert "jon.smyth@globex.net" in emails


# --------------------------------------------------------------------------- #
# Missing-source robustness
# --------------------------------------------------------------------------- #
def test_missing_source_records_error_and_continues(config: ProjectionConfig) -> None:
    """A missing source produces an ingest Error_Report; the run still completes."""
    refs = [
        SourceRef(location=str(_SAMPLES / "does_not_exist.csv")),
        SourceRef(location=str(_SAMPLES / "recruiter.csv")),
        SourceRef(location=str(_SAMPLES / "ats.json")),
    ]
    result = TransformerEngine().run(refs, config)

    assert isinstance(result, RunResult)
    # The run continued and still produced profiles from the valid sources.
    assert result.profiles
    # An ingest-stage Error_Report names the failing source.
    ingest_errors = [e for e in result.errors if e.stage == "ingest"]
    assert ingest_errors, "expected an ingest Error_Report for the missing source"
    assert any("does_not_exist" in (e.source or "") for e in ingest_errors)
    # Any source/projection error drives a non-zero exit code.
    assert result.exit_code != 0


def test_all_sources_missing_emits_all_null_record(config: ProjectionConfig) -> None:
    """When every source fails, the engine still returns a RunResult and reports errors."""
    refs = [SourceRef(location=str(_SAMPLES / "nope_a.csv"))]
    result = TransformerEngine().run(refs, config)

    assert isinstance(result, RunResult)
    assert any(e.stage == "ingest" for e in result.errors)
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_two_runs_produce_identical_profiles(config: ProjectionConfig) -> None:
    """Identical inputs + config produce identical projected profiles (Req 12.1)."""
    refs = _sample_refs()
    first = TransformerEngine().run(refs, config)
    second = TransformerEngine().run(refs, config)
    assert first.profiles == second.profiles


def test_input_order_independent_profiles(config: ProjectionConfig) -> None:
    """Reordering the input sources does not change the produced profiles."""
    refs = _sample_refs()
    forward = TransformerEngine().run(refs, config)
    reverse = TransformerEngine().run(list(reversed(refs)), config)

    def _key(profile: dict) -> str:
        return str(profile.get("id"))

    assert sorted(forward.profiles, key=_key) == sorted(reverse.profiles, key=_key)

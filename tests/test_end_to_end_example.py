"""End-to-end integration test that produces committed example output (task 16.3).

This test exercises the whole pipeline through the public
:meth:`TransformerEngine.run` entry point over the four sample fixtures
(``recruiter.csv``, ``ats.json``, ``resume_jane_doe.docx``, ``notes.txt``) using
the shipped projection configs in ``samples/configs/``. It asserts that:

* profiles and a clean run result are produced (Req 13.1, 14.1),
* the overlapping candidate (Jane Doe), who appears in all four sources, is merged
  into a single identity group / profile (Req 4, 14.1),
* each profile's structure matches its config -- selected/renamed fields, and the
  ``include_provenance`` / ``include_confidence`` toggles (Req 8),
* running the same inputs + config twice yields identical profiles (Req 12.1), and
* the resulting JSON written to ``samples/output/`` round-trips back to the
  in-memory result (Req 13.2).

The two written files (``samples/output/example_default.json`` and
``samples/output/example_custom.json``) serve as committed example output produced
deterministically via the model serialization helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from candidate_transformer.engine.projection import ProjectionConfig
from candidate_transformer.engine.transformer import TransformerEngine
from candidate_transformer.models.run_result import RunResult
from candidate_transformer.models.serialization import run_result_to_dict

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples"
CONFIGS = SAMPLES / "configs"
OUTPUT_DIR = SAMPLES / "output"

# The four sample fixtures that together describe overlapping candidates and
# exercise identity matching (Jane Doe appears in every one).
FIXTURES = [
    SAMPLES / "recruiter.csv",
    SAMPLES / "ats.json",
    SAMPLES / "resume_jane_doe.docx",
    SAMPLES / "notes.txt",
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_config(name: str) -> ProjectionConfig:
    """Parse a shipped projection config from ``samples/configs/``."""
    with (CONFIGS / name).open(encoding="utf-8") as handle:
        return ProjectionConfig.from_dict(json.load(handle))


def _run(config: ProjectionConfig) -> RunResult:
    """Run the full pipeline over the four sample fixtures with ``config``."""
    refs = [str(path) for path in FIXTURES]
    return TransformerEngine().run(refs, config)


def _names(result: RunResult, key: str) -> list[str]:
    """Collect the candidate name under ``key`` from each profile."""
    return [profile[key] for profile in result.profiles if key in profile]


@pytest.fixture(scope="module", autouse=True)
def _ensure_output_dir() -> None:
    """Create the committed example-output directory before the tests run."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def test_fixtures_exist() -> None:
    """The four sample fixtures the test relies on are present."""
    for path in FIXTURES:
        assert path.is_file(), f"missing sample fixture: {path}"


# --------------------------------------------------------------------------- #
# Default config: produces example_default.json
# --------------------------------------------------------------------------- #
def test_end_to_end_default_config_produces_example_output() -> None:
    config = _load_config("default.json")
    result = _run(config)

    # A clean run that produced profiles (Req 13.1, 14.1).
    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.errors == []
    assert len(result.profiles) >= 1

    # The overlapping candidate is merged into exactly one profile (Req 4, 14.1).
    names = _names(result, "name")
    assert names.count("Jane Doe") == 1

    # default.json: include_provenance=false, include_confidence=true. Every profile
    # honors the toggles and carries the always-present selected fields (Req 8.3,
    # 8.11, 8.12).
    for profile in result.profiles:
        assert "provenance" not in profile
        assert "overall_confidence" in profile
        # required + on_missing="null"/array fields are always present.
        assert "id" in profile
        assert "name" in profile
        assert "primary_email" in profile
        assert "skills" in profile and isinstance(profile["skills"], list)
        assert "experience" in profile and isinstance(profile["experience"], list)

    # Jane Doe's merged profile reflects values drawn across the sources (Req 5, 8).
    jane = next(p for p in result.profiles if p.get("name") == "Jane Doe")
    assert jane["primary_email"] == "jane.doe@example.com"
    assert jane["country"] == "US"  # resolved -> present despite on_missing="omit"
    assert "Python" in jane["skills"]
    # linkedin is renamed-through + lowercase-normalized (Req 8.8, 8.10).
    assert "linkedin" in jane
    assert jane["linkedin"] == jane["linkedin"].lower()

    # Write committed example output and assert it round-trips (Req 13.2, 12.1).
    _assert_written_output_round_trips(result, OUTPUT_DIR / "example_default.json")


# --------------------------------------------------------------------------- #
# Custom config: produces example_custom.json
# --------------------------------------------------------------------------- #
def test_end_to_end_custom_config_produces_example_output() -> None:
    config = _load_config("custom.json")
    result = _run(config)

    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.errors == []
    assert len(result.profiles) >= 1

    # Overlapping candidate merged into a single profile, under the renamed key.
    candidates = _names(result, "candidate")
    assert candidates.count("Jane Doe") == 1

    # custom.json: include_provenance=true, include_confidence=false, with renamed
    # output field names (Req 8.4, 8.8, 8.11, 8.12).
    for profile in result.profiles:
        assert "provenance" in profile
        assert "overall_confidence" not in profile
        assert "candidate" in profile
        assert "work_email" in profile
        assert "city" in profile
        assert "headline" in profile

    jane = next(p for p in result.profiles if p.get("candidate") == "Jane Doe")
    assert jane["work_email"] == "jane.doe@example.com"
    # github_url is renamed-through + lowercase-normalized (Req 8.8, 8.10).
    assert "github_url" in jane
    assert jane["github_url"] == jane["github_url"].lower()

    _assert_written_output_round_trips(result, OUTPUT_DIR / "example_custom.json")


# --------------------------------------------------------------------------- #
# Determinism (Req 12.1)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("config_name", ["default.json", "custom.json"])
def test_end_to_end_is_deterministic(config_name: str) -> None:
    """Running the same fixtures + config twice yields identical profiles (Req 12.1)."""
    first = _run(_load_config(config_name))
    second = _run(_load_config(config_name))

    assert first.profiles == second.profiles
    assert run_result_to_dict(first) == run_result_to_dict(second)


# --------------------------------------------------------------------------- #
# Shared write + round-trip assertion
# --------------------------------------------------------------------------- #
def _assert_written_output_round_trips(result: RunResult, path: Path) -> None:
    """Serialize ``result`` deterministically to ``path`` and assert it round-trips.

    Uses the model serialization helper so the written JSON is JSON-ready and
    deterministic (Req 12.1), then reads it back and confirms the parsed content
    matches the in-memory serialized result (Req 13.2).
    """
    serialized = run_result_to_dict(result)
    text = json.dumps(serialized, indent=2, ensure_ascii=False, sort_keys=False)
    path.write_text(text, encoding="utf-8")

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == json.loads(json.dumps(serialized, ensure_ascii=False))
    # The profiles survive the JSON round-trip unchanged.
    assert reloaded["profiles"] == result.profiles
    assert reloaded["exit_code"] == result.exit_code

"""Logging-level unit tests for the TransformerEngine (task 14.5).

These are example/unit tests (not property-based) that assert the engine emits
structured ``Log_Entry`` records at the three distinct ``Logging_Level``s described
in the design's "Structured Logging" section:

* **INFO** for normal progress events -- source loaded, identity groups resolved,
  profile emitted (Req 11.4).
* **WARNING** for recoverable conditions -- an empty source that produces no
  records, or a run with no per-source records (Req 11.5).
* **ERROR** alongside every ``Error_Report`` -- e.g. a missing/unresolvable source
  reported at the ingest stage (Req 11.6).

Each emitted entry is also checked for the canonical ``Log_Entry`` shape
``{timestamp, level, module, message}`` with a level drawn from the fixed set
``{INFO, WARNING, ERROR}`` (Req 11.3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine import ProjectionConfig, TransformerEngine
from candidate_transformer.models import LogEntry

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLES = _REPO_ROOT / "samples"
_DEFAULT_CONFIG_PATH = _SAMPLES / "configs" / "default.json"

_VALID_LEVELS = {"INFO", "WARNING", "ERROR"}


def _default_config() -> ProjectionConfig:
    """Load the default projection config from the samples directory."""
    return ProjectionConfig.from_dict(
        json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    )


def _sample_refs() -> list[SourceRef]:
    """The four overlapping sample sources, routed to their adapters."""
    return [
        SourceRef(location=str(_SAMPLES / "recruiter.csv")),
        SourceRef(location=str(_SAMPLES / "ats.json")),
        SourceRef(location=str(_SAMPLES / "resume_jane_doe.docx")),
        SourceRef(location=str(_SAMPLES / "notes.txt")),
    ]


def _levels_for_module(logs: list[LogEntry], module: str) -> list[LogEntry]:
    return [e for e in logs if e.module == module]


@pytest.fixture()
def config() -> ProjectionConfig:
    return _default_config()


# --------------------------------------------------------------------------- #
# Log_Entry shape (Req 11.3)
# --------------------------------------------------------------------------- #
def test_log_entries_have_canonical_shape(config: ProjectionConfig) -> None:
    """Every emitted Log_Entry has {timestamp, level, module, message} (Req 11.3)."""
    engine = TransformerEngine()
    engine.run(_sample_refs(), config)

    assert engine.logs, "expected the engine to emit log entries during a run"
    for entry in engine.logs:
        assert isinstance(entry, LogEntry)
        # Shape: every field is a non-empty string.
        assert isinstance(entry.timestamp, str) and entry.timestamp
        assert isinstance(entry.module, str) and entry.module
        assert isinstance(entry.message, str) and entry.message
        # Level is one of the fixed Logging_Levels.
        assert entry.level in _VALID_LEVELS


# --------------------------------------------------------------------------- #
# INFO -- normal progress events (Req 11.4)
# --------------------------------------------------------------------------- #
def test_info_progress_events_emitted_on_clean_run(config: ProjectionConfig) -> None:
    """A clean run emits INFO entries for the key progress milestones (Req 11.4)."""
    engine = TransformerEngine()
    result = engine.run(_sample_refs(), config)
    assert result.errors == [], f"expected a clean run, got: {result.errors}"

    info = [e for e in engine.logs if e.level == "INFO"]
    assert info, "expected INFO progress entries on a clean run"

    # A source was loaded (ingest stage).
    assert any(
        e.module == "ingest" and "loaded" in e.message for e in info
    ), "expected an INFO entry for a loaded source"

    # Identity groups were resolved (resolve stage).
    assert any(
        e.module == "resolve" and "identity group" in e.message for e in info
    ), "expected an INFO entry for resolved identity groups"

    # A profile was emitted (project stage).
    assert any(
        e.module == "project" and "emitted profile" in e.message for e in info
    ), "expected an INFO entry for an emitted profile"


# --------------------------------------------------------------------------- #
# WARNING -- recoverable conditions (Req 11.5)
# --------------------------------------------------------------------------- #
def test_warning_emitted_for_empty_source(
    tmp_path: Path, config: ProjectionConfig
) -> None:
    """An empty source produces no records -> a WARNING is emitted (Req 11.5, 10.2)."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")

    engine = TransformerEngine()
    engine.run([SourceRef(location=str(empty_csv))], config)

    warnings = [e for e in engine.logs if e.level == "WARNING"]
    assert warnings, "expected a WARNING for the empty source"
    # The recoverable condition is reported with a descriptive message.
    assert any(
        "no records" in e.message or "all-null" in e.message for e in warnings
    ), f"expected an empty-source WARNING, got: {[w.message for w in warnings]}"


def test_warning_emitted_when_no_records_available(config: ProjectionConfig) -> None:
    """A run with no usable sources warns and still emits an all-null record (Req 11.5)."""
    engine = TransformerEngine()
    engine.run([], config)

    warnings = [e for e in engine.logs if e.level == "WARNING"]
    assert any(
        e.module == "merge" and "all-null" in e.message for e in warnings
    ), f"expected an all-null-record WARNING, got: {[w.message for w in warnings]}"


# --------------------------------------------------------------------------- #
# ERROR -- emitted alongside every Error_Report (Req 11.6)
# --------------------------------------------------------------------------- #
def test_error_emitted_for_missing_source(config: ProjectionConfig) -> None:
    """A missing source yields an ingest Error_Report AND a paired ERROR log (Req 11.6)."""
    refs = [
        SourceRef(location=str(_SAMPLES / "does_not_exist.csv")),
        SourceRef(location=str(_SAMPLES / "recruiter.csv")),
    ]
    engine = TransformerEngine()
    result = engine.run(refs, config)

    # A structured ingest Error_Report was recorded.
    ingest_errors = [e for e in result.errors if e.stage == "ingest"]
    assert ingest_errors, "expected an ingest Error_Report for the missing source"

    # A corresponding ERROR-level log entry was emitted at the ingest module.
    error_logs = [e for e in engine.logs if e.level == "ERROR"]
    assert error_logs, "expected an ERROR log paired with the Error_Report"
    assert any(
        e.module == "ingest" for e in error_logs
    ), "expected the ERROR log to come from the ingest stage"

    # Every Error_Report has at least one matching ERROR log (Req 11.6).
    assert len(error_logs) >= len(result.errors)

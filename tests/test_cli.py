"""Tests for the thin command-line interface over ``TransformerEngine.run`` (Req 13).

These are example/integration tests (not property tests): they invoke ``main()``
directly with a constructed argv and assert exit codes, stdout vs. file output, and
error messages. They exercise the four scenarios the design calls out -- with and
without ``--output``, a missing ``--input``, and an unparseable ``--config`` -- plus
the success/error exit-code contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from candidate_transformer.cli.main import main

# A minimal, valid Projection_Config: select a couple of always-present canonical
# fields so a clean run produces deterministic, error-free output.
_CONFIG = {
    "include_provenance": False,
    "include_confidence": True,
    "fields": [
        {"name": "id", "from": "candidate_id", "type": "string", "on_missing": "null"},
        {"name": "name", "from": "full_name", "type": "string", "on_missing": "null"},
    ],
}


def _write_config(tmp_path: Path, data: object) -> str:
    """Write ``data`` as JSON to a temp config file and return its path string."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _write_recruiter_csv(tmp_path: Path) -> str:
    """Write a tiny one-row recruiter CSV and return its path string."""
    path = tmp_path / "recruiter.csv"
    path.write_text(
        "name,email,phone,current_company,title\n"
        "Jane Doe,jane@example.com,+14155552671,Acme,Engineer\n",
        encoding="utf-8",
    )
    return str(path)


def test_prints_json_to_stdout_without_output(tmp_path, capsys):
    """Without ``--output`` the CLI prints JSON profiles to stdout and exits 0 (Req 13.1, 13.3, 13.6)."""
    config = _write_config(tmp_path, _CONFIG)
    source = _write_recruiter_csv(tmp_path)

    exit_code = main(["--input", source, "--config", config])

    assert exit_code == 0
    captured = capsys.readouterr()
    profiles = json.loads(captured.out)
    assert isinstance(profiles, list)
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Jane Doe"
    # No error reports on a clean run.
    assert captured.err == ""


def test_writes_json_to_output_file(tmp_path, capsys):
    """With ``--output`` the CLI writes JSON to the file and not to stdout (Req 13.2)."""
    config = _write_config(tmp_path, _CONFIG)
    source = _write_recruiter_csv(tmp_path)
    out_path = tmp_path / "profiles.json"

    exit_code = main(
        ["--input", source, "--config", config, "--output", str(out_path)]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    # Nothing meaningful printed to stdout (output went to the file).
    assert captured.out.strip() == ""
    assert out_path.exists()
    profiles = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(profiles, list)
    assert profiles[0]["name"] == "Jane Doe"


def test_missing_input_is_usage_error(tmp_path, capsys):
    """A missing required ``--input`` yields a usage error naming it, non-zero exit (Req 13.4)."""
    config = _write_config(tmp_path, _CONFIG)

    exit_code = main(["--config", config])

    assert exit_code != 0
    captured = capsys.readouterr()
    # argparse prints usage + the offending argument name to stderr.
    assert "--input" in captured.err


def test_unparseable_config_is_configuration_error(tmp_path, capsys):
    """An unparseable ``--config`` yields a configuration error naming the problem (Req 13.5)."""
    bad_config = tmp_path / "broken.json"
    bad_config.write_text("{ this is not valid json", encoding="utf-8")
    source = _write_recruiter_csv(tmp_path)

    exit_code = main(["--input", source, "--config", str(bad_config)])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "configuration error" in captured.err.lower()


def test_structurally_invalid_config_is_configuration_error(tmp_path, capsys):
    """A structurally invalid config (bad field entry) is a configuration error (Req 13.5)."""
    # A field entry with neither 'name' nor 'path' is rejected by from_dict.
    invalid = {"fields": [{"type": "string"}]}
    config = _write_config(tmp_path, invalid)
    source = _write_recruiter_csv(tmp_path)

    exit_code = main(["--input", source, "--config", config])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "configuration error" in captured.err.lower()


def test_missing_config_file_is_configuration_error(tmp_path, capsys):
    """A non-existent ``--config`` path is reported as a configuration error (Req 13.5)."""
    source = _write_recruiter_csv(tmp_path)
    missing = str(tmp_path / "does_not_exist.json")

    exit_code = main(["--input", source, "--config", missing])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "configuration error" in captured.err.lower()


def test_run_errors_exit_nonzero_and_report_to_stderr(tmp_path, capsys):
    """A run with source/projection errors exits non-zero and prints Error_Reports to stderr (Req 13.7)."""
    config = _write_config(tmp_path, _CONFIG)
    # A missing source file -> an ingest Error_Report; the run still completes.
    missing_source = str(tmp_path / "nope.csv")

    exit_code = main(["--input", missing_source, "--config", config])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "error_report" in captured.err.lower()
    # stdout still carries valid JSON (an all-null profile).
    profiles = json.loads(captured.out)
    assert isinstance(profiles, list)


def test_multiple_inputs_are_all_passed_to_the_engine(tmp_path, capsys):
    """Multiple ``--input`` references run together and produce one profile per identity (Req 13.1)."""
    config = _write_config(tmp_path, _CONFIG)
    source = _write_recruiter_csv(tmp_path)
    notes = tmp_path / "notes.txt"
    notes.write_text("Jane Doe is a great engineer.\n", encoding="utf-8")

    exit_code = main(
        ["--input", source, "--input", str(notes), "--config", config]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    profiles = json.loads(captured.out)
    assert isinstance(profiles, list)
    assert len(profiles) >= 1


def test_output_is_deterministic(tmp_path, capsys):
    """Identical inputs yield byte-identical JSON output across runs (Req 12.1)."""
    config = _write_config(tmp_path, _CONFIG)
    source = _write_recruiter_csv(tmp_path)

    main(["--input", source, "--config", config])
    first = capsys.readouterr().out

    main(["--input", source, "--config", config])
    second = capsys.readouterr().out

    assert first == second


def test_typed_input_prefix_routes_to_github_adapter(tmp_path, capsys):
    """An 'github=PATH' input prefix forces the GitHub adapter for a local JSON (Req 13.1)."""
    config = _write_config(tmp_path, _CONFIG)
    payload = tmp_path / "profile.json"
    payload.write_text(
        json.dumps(
            {
                "login": "octocat",
                "name": "Octo Cat",
                "languages": ["Python", "Go"],
                "html_url": "https://github.com/octocat",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        ["--input", f"github={payload}", "--config", config]
    )

    assert exit_code == 0
    profiles = json.loads(capsys.readouterr().out)
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Octo Cat"


def test_plain_input_without_prefix_still_works(tmp_path, capsys):
    """An input with no type prefix is treated as a plain path (back-compat)."""
    config = _write_config(tmp_path, _CONFIG)
    source = _write_recruiter_csv(tmp_path)

    exit_code = main(["--input", source, "--config", config])

    assert exit_code == 0
    profiles = json.loads(capsys.readouterr().out)
    assert profiles[0]["name"] == "Jane Doe"

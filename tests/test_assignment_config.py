"""Compatibility tests for the assignment's exact Projection_Config dialect.

The assignment's example config uses a slightly different vocabulary than our
native one: ``"path"`` for the output field name, an optional ``"from"`` (omitted
when the source path equals the output name), a top-level global ``"on_missing"``,
``"string[]"`` array-type notation, and ``"normalize": "E164" / "canonical"``. These
tests confirm our engine accepts that dialect and projects + validates correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from candidate_transformer.engine.projection import ProjectionConfig, project
from candidate_transformer.engine.validation import validate
from candidate_transformer.models.canonical import (
    CanonicalRecord,
    Skill,
)

_CONFIGS = Path(__file__).resolve().parent.parent / "samples" / "configs"


def _record() -> CanonicalRecord:
    return CanonicalRecord(
        candidate_id="cid-1",
        full_name="Jane Doe",
        emails=["jane@example.com"],
        phones=["+14155552671"],
        skills=[Skill(name="Python", confidence=0.9, sources=["s1"])],
        overall_confidence=0.9,
    )


def test_assignment_example_config_parses_and_projects():
    """The shipped assignment-format config parses, projects, and validates clean."""
    config = ProjectionConfig.from_dict(
        json.loads((_CONFIGS / "assignment_example.json").read_text(encoding="utf-8"))
    )
    profile, errors = project(_record(), config)

    assert errors == []
    # 'path' became the output name; 'from' omitted -> source == name.
    assert profile["full_name"] == "Jane Doe"
    assert profile["primary_email"] == "jane@example.com"
    assert profile["phone"] == "+14155552671"  # normalize E164 (idempotent here)
    assert profile["skills"] == ["Python"]      # normalize canonical
    # include_confidence: true -> overall_confidence present.
    assert "overall_confidence" in profile

    assert validate(profile, config) == []


def test_global_on_missing_default_applies_to_fields():
    """A top-level on_missing is the default for fields that don't set their own."""
    config = ProjectionConfig.from_dict(
        {
            "on_missing": "omit",
            "fields": [
                {"path": "headline"},               # absent -> omit (global)
                {"path": "full_name"},              # present
            ],
        }
    )
    profile, errors = project(_record(), config)
    assert errors == []
    assert "headline" not in profile               # omitted via global default
    assert profile["full_name"] == "Jane Doe"


def test_per_field_on_missing_overrides_global():
    config = ProjectionConfig.from_dict(
        {
            "on_missing": "omit",
            "fields": [{"path": "headline", "on_missing": "null"}],
        }
    )
    profile, _ = project(_record(), config)
    assert profile["headline"] is None             # per-field null beats global omit


def test_string_array_type_notation_validates():
    """'string[]' type notation is treated as an array of strings by the validator."""
    config = ProjectionConfig.from_dict(
        {
            "include_confidence": False,
            "include_provenance": False,
            "fields": [
                {"path": "skills", "from": "skills[].name", "type": "string[]"}
            ],
        }
    )
    profile, errors = project(_record(), config)
    assert errors == []
    assert profile["skills"] == ["Python"]
    assert validate(profile, config) == []

    # A non-list value for a string[] field is a validation error.
    bad = {"skills": "not-a-list"}
    assert any(e.field == "skills" for e in validate(bad, config))


def test_full_default_config_emits_all_canonical_fields():
    """The full-default config projects the complete canonical schema."""
    config = ProjectionConfig.from_dict(
        json.loads((_CONFIGS / "full_default.json").read_text(encoding="utf-8"))
    )
    profile, errors = project(_record(), config)
    assert errors == []
    for key in (
        "candidate_id",
        "full_name",
        "emails",
        "phones",
        "location",
        "links",
        "headline",
        "years_experience",
        "skills",
        "experience",
        "education",
        "provenance",
        "overall_confidence",
    ):
        assert key in profile
    assert validate(profile, config) == []

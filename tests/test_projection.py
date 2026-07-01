"""Unit tests for the config-driven projection engine (Task 11.2).

Covers field selection/subset, rename/remap, nested/indexed/array-projection
``from`` paths, per-field normalization, ``on_missing`` null/omit/error, invalid
paths -> projection errors, the provenance/confidence toggles, and a no-mutation
check (Req 8.1-8.16, 15.1, 15.3). Also loads ``samples/configs/default.json`` as
an integration-ish check that the real config shape parses and projects.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from candidate_transformer.engine.projection import (
    FieldSpec,
    ProjectionConfig,
    ProjectionConfigError,
    ProjectionEngine,
    project,
)
from candidate_transformer.models.canonical import (
    CanonicalRecord,
    ExperienceEntry,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "samples" / "configs"


def _sample_record() -> CanonicalRecord:
    """A fully-populated canonical record exercising every path kind."""
    return CanonicalRecord(
        candidate_id="cid-123",
        full_name="Jane Doe",
        emails=["jane@example.com", "j.doe@work.com"],
        phones=["+14155552671"],
        location=Location(city="Seattle", region="WA", country="US"),
        links=Links(
            linkedin="HTTPS://LinkedIn.com/in/Jane",
            github="HTTPS://GitHub.com/Jane",
            other=["https://x.com/jane"],
        ),
        headline="Engineer",
        years_experience=7.0,
        skills=[
            Skill(name="Python", confidence=0.9, sources=["src-1"]),
            Skill(name="JavaScript", confidence=0.8, sources=["src-2"]),
        ],
        experience=[
            ExperienceEntry(company="Acme", title="Dev", start="2020-01", end="2022-03"),
        ],
        provenance=[
            ProvenanceEntry(
                field="full_name",
                value="Jane Doe",
                source="src-1",
                method="csv_column",
                confidence=0.9,
            ),
        ],
        overall_confidence=0.85,
    )


# --- Field selection / subset (Req 8.3) ------------------------------------


def test_only_selected_fields_appear():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[
            FieldSpec(name="name", from_="full_name"),
            FieldSpec(name="headline", from_="headline"),
        ],
        include_provenance=False,
        include_confidence=False,
    )

    profile, errors = project(record, config)

    assert errors == []
    assert profile == {"name": "Jane Doe", "headline": "Engineer"}


# --- Rename / remap (Req 8.4, 8.8) -----------------------------------------


def test_rename_places_value_under_output_name():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="id", from_="candidate_id")],
        include_provenance=False,
        include_confidence=False,
    )

    profile, errors = project(record, config)

    assert profile == {"id": "cid-123"}
    assert errors == []


# --- Nested / indexed / array-projection from-paths (Req 8.5, 8.6, 8.7) ----


def test_nested_path_resolves_subfield():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="country", from_="location.country")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile == {"country": "US"}


def test_indexed_path_reads_element():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="primary_email", from_="emails[0]")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile == {"primary_email": "jane@example.com"}


def test_array_projection_collects_subfield():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="skills", from_="skills[].name")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile == {"skills": ["Python", "JavaScript"]}


def test_object_array_serialized_to_dicts():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="experience", from_="experience")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile["experience"] == [
        {
            "company": "Acme",
            "title": "Dev",
            "start": "2020-01",
            "end": "2022-03",
            "summary": None,
        }
    ]


# --- Per-field normalize (Req 8.10) ----------------------------------------


def test_normalize_lowercase_on_scalar():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="linkedin", from_="links.linkedin", normalize="lowercase")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile == {"linkedin": "https://linkedin.com/in/jane"}


def test_normalize_uppercase_on_array_elements():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="skills", from_="skills[].name", normalize="uppercase")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert profile == {"skills": ["PYTHON", "JAVASCRIPT"]}


# --- on_missing: null / omit / error (Req 8.13, 8.14, 8.15) ----------------


def test_on_missing_null_emits_null():
    record = CanonicalRecord(candidate_id="cid")  # full_name is None
    config = ProjectionConfig(
        fields=[FieldSpec(name="name", from_="full_name", on_missing="null")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert profile == {"name": None}
    assert errors == []


def test_on_missing_omit_drops_field():
    record = CanonicalRecord(candidate_id="cid")  # headline is None
    config = ProjectionConfig(
        fields=[FieldSpec(name="headline", from_="headline", on_missing="omit")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert profile == {}
    assert errors == []


def test_on_missing_error_reports_projection_error():
    record = CanonicalRecord()  # candidate_id is None
    config = ProjectionConfig(
        fields=[
            FieldSpec(name="id", from_="candidate_id", required=True, on_missing="error"),
        ],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert "id" not in profile
    assert len(errors) == 1
    assert errors[0].stage == "project"
    assert "id" in errors[0].error


def test_empty_list_index_is_treated_as_missing():
    record = CanonicalRecord(candidate_id="cid")  # emails == []
    config = ProjectionConfig(
        fields=[FieldSpec(name="primary_email", from_="emails[0]", on_missing="null")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert profile == {"primary_email": None}
    assert errors == []


# --- Invalid path -> projection error (Req 8.16) ---------------------------


def test_out_of_range_index_reports_invalid_path_error():
    record = _sample_record()  # phones has length 1
    config = ProjectionConfig(
        fields=[FieldSpec(name="p", from_="phones[50]", on_missing="null")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert "p" not in profile
    assert len(errors) == 1
    assert errors[0].stage == "project"
    assert "phones[50]" in errors[0].error


def test_nonexistent_subfield_reports_invalid_path_error():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="s", from_="skills[].abc", on_missing="null")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, errors = project(record, config)
    assert "s" not in profile
    assert len(errors) == 1
    assert "skills[].abc" in errors[0].error


# --- Provenance / confidence toggles (Req 8.11, 8.12) ----------------------


def test_provenance_and_confidence_included_when_on():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="name", from_="full_name")],
        include_provenance=True,
        include_confidence=True,
    )
    profile, _ = project(record, config)
    assert profile["overall_confidence"] == 0.85
    assert profile["provenance"] == [
        {
            "field": "full_name",
            "value": "Jane Doe",
            "source": "src-1",
            "method": "csv_column",
            "confidence": 0.9,
        }
    ]


def test_provenance_and_confidence_omitted_when_off():
    record = _sample_record()
    config = ProjectionConfig(
        fields=[FieldSpec(name="name", from_="full_name")],
        include_provenance=False,
        include_confidence=False,
    )
    profile, _ = project(record, config)
    assert "provenance" not in profile
    assert "overall_confidence" not in profile


# --- No mutation (Req 8.2, 15.1, 15.3) -------------------------------------


def test_projection_does_not_mutate_canonical_record():
    record = _sample_record()
    snapshot = copy.deepcopy(record)
    config = ProjectionConfig(
        fields=[
            FieldSpec(name="skills", from_="skills[].name", normalize="uppercase"),
            FieldSpec(name="experience", from_="experience"),
            FieldSpec(name="linkedin", from_="links.linkedin", normalize="lowercase"),
        ],
        include_provenance=True,
        include_confidence=True,
    )

    project(record, config)

    assert record == snapshot


def test_multiple_configs_share_unchanged_record():
    record = _sample_record()
    snapshot = copy.deepcopy(record)
    cfg_a = ProjectionConfig(fields=[FieldSpec(name="n", from_="full_name")])
    cfg_b = ProjectionConfig(fields=[FieldSpec(name="c", from_="location.country")])

    engine = ProjectionEngine()
    engine.project(record, cfg_a)
    engine.project(record, cfg_b)

    assert record == snapshot


# --- Config parsing --------------------------------------------------------


def test_config_from_dict_parses_toggles_and_fields():
    config = ProjectionConfig.from_dict(
        {
            "include_provenance": False,
            "include_confidence": True,
            "fields": [
                {"name": "id", "from": "candidate_id", "required": True, "on_missing": "error"},
            ],
        }
    )
    assert config.include_provenance is False
    assert config.include_confidence is True
    assert config.fields[0].name == "id"
    assert config.fields[0].required is True
    assert config.fields[0].on_missing == "error"


def test_config_retains_type_enum_element_type_for_validation():
    config = ProjectionConfig.from_dict(
        {
            "fields": [
                {
                    "name": "country",
                    "from": "location.country",
                    "type": "string",
                    "enum": ["US", "IN", "GB"],
                },
                {
                    "name": "experience",
                    "from": "experience",
                    "type": "array",
                    "element_type": {"company": "string", "title": "string"},
                },
            ]
        }
    )
    assert config.fields[0].type == "string"
    assert config.fields[0].enum == ["US", "IN", "GB"]
    assert config.fields[1].element_type == {"company": "string", "title": "string"}


def test_config_missing_name_raises():
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig.from_dict({"fields": [{"from": "full_name"}]})


def test_config_from_defaults_to_name_when_absent():
    # New semantics: a field without an explicit 'from' uses its own output name as
    # the source path (supports the assignment's {"path": "full_name"} form).
    config = ProjectionConfig.from_dict({"fields": [{"name": "full_name"}]})
    assert config.fields[0].name == "full_name"
    assert config.fields[0].from_ == "full_name"


def test_config_path_alias_is_accepted():
    # The assignment example uses 'path' for the output field name.
    config = ProjectionConfig.from_dict(
        {"fields": [{"path": "primary_email", "from": "emails[0]"}]}
    )
    assert config.fields[0].name == "primary_email"
    assert config.fields[0].from_ == "emails[0]"


def test_config_missing_name_and_path_raises():
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig.from_dict({"fields": [{"type": "string"}]})


def test_config_invalid_on_missing_raises():
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig.from_dict(
            {"fields": [{"name": "x", "from": "full_name", "on_missing": "bogus"}]}
        )


def test_config_invalid_normalize_raises():
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig.from_dict(
            {"fields": [{"name": "x", "from": "full_name", "normalize": "rot13"}]}
        )


# --- Integration with the real sample config -------------------------------


def test_default_sample_config_projects_full_record():
    record = _sample_record()
    data = json.loads((CONFIG_DIR / "default.json").read_text(encoding="utf-8"))
    config = ProjectionConfig.from_dict(data)

    profile, errors = project(record, config)

    assert errors == []
    # include_provenance: false, include_confidence: true
    assert "provenance" not in profile
    assert profile["overall_confidence"] == 0.85
    assert profile["id"] == "cid-123"
    assert profile["name"] == "Jane Doe"
    assert profile["primary_email"] == "jane@example.com"
    assert profile["country"] == "US"
    assert profile["skills"] == ["Python", "JavaScript"]
    # linkedin uses normalize: lowercase
    assert profile["linkedin"] == "https://linkedin.com/in/jane"


def test_default_sample_config_required_field_error_when_absent():
    record = CanonicalRecord()  # candidate_id None -> id uses on_missing error
    data = json.loads((CONFIG_DIR / "default.json").read_text(encoding="utf-8"))
    config = ProjectionConfig.from_dict(data)

    profile, errors = project(record, config)

    assert "id" not in profile
    assert any("id" in e.error and e.stage == "project" for e in errors)
    # country uses omit -> absent; linkedin uses omit -> absent
    assert "country" not in profile
    assert "linkedin" not in profile

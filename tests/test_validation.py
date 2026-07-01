"""Example unit tests for the output schema Validation_Module (Req 9).

Covers a conforming profile (validation passes) and one injected violation of each
kind: wrong type, missing required field, out-of-enum value, non-list for an array
field, a bad element type in an array, and a missing/mistyped object subfield. Each
failure must be reported as a structured :class:`ValidationError` naming the
offending field and the reason (Req 9.2).
"""

from __future__ import annotations

from candidate_transformer.engine.projection import ProjectionConfig
from candidate_transformer.engine.validation import (
    ValidationError,
    Validator,
    validate,
)


def _config() -> ProjectionConfig:
    """A representative inline schema exercising every validation check."""
    return ProjectionConfig.from_dict(
        {
            "include_provenance": False,
            "include_confidence": False,
            "fields": [
                {"name": "id", "from": "candidate_id", "type": "string", "required": True},
                {
                    "name": "country",
                    "from": "location.country",
                    "type": "string",
                    "enum": ["US", "IN", "GB"],
                },
                {
                    "name": "skills",
                    "from": "skills[].name",
                    "type": "array",
                    "element_type": "string",
                },
                {
                    "name": "experience",
                    "from": "experience",
                    "type": "array",
                    "element_type": {"company": "string", "title": "string"},
                },
                {
                    "name": "location",
                    "from": "location",
                    "type": "object",
                    "element_type": {"city": "string", "country": "string"},
                },
            ],
        }
    )


def _conforming_profile() -> dict:
    """A profile that conforms to :func:`_config`'s schema."""
    return {
        "id": "abc-123",
        "country": "US",
        "skills": ["Python", "JavaScript"],
        "experience": [
            {"company": "Acme", "title": "Engineer"},
            {"company": "Globex", "title": "Lead"},
        ],
        "location": {"city": "Seattle", "country": "US"},
    }


def test_conforming_profile_passes() -> None:
    """A profile matching every declared field schema produces no errors (Req 9.1)."""
    errors = validate(_conforming_profile(), _config())
    assert errors == []


def test_type_mismatch_is_reported() -> None:
    """A scalar value of the wrong type fails the type check (Req 9.3)."""
    profile = _conforming_profile()
    profile["id"] = 123  # declared "string"

    errors = validate(profile, _config())

    assert any(e.field == "id" and "type" in e.reason for e in errors)


def test_missing_required_field_is_reported() -> None:
    """An absent required field fails the presence check (Req 9.4)."""
    profile = _conforming_profile()
    del profile["id"]  # declared required

    errors = validate(profile, _config())

    assert any(e.field == "id" and "missing" in e.reason for e in errors)


def test_out_of_enum_value_is_reported() -> None:
    """A value outside the declared enum set fails the enum check (Req 9.5)."""
    profile = _conforming_profile()
    profile["country"] = "FR"  # not in ["US", "IN", "GB"]

    errors = validate(profile, _config())

    assert any(e.field == "country" and "allowed set" in e.reason for e in errors)


def test_non_list_for_array_field_is_reported() -> None:
    """A non-list value for an array field fails the type check (Req 9.6)."""
    profile = _conforming_profile()
    profile["skills"] = "Python"  # declared "array"

    errors = validate(profile, _config())

    assert any(e.field == "skills" and "type" in e.reason for e in errors)


def test_bad_element_type_in_array_is_reported() -> None:
    """A wrong-typed element of an array fails the element-type check (Req 9.6)."""
    profile = _conforming_profile()
    profile["skills"] = ["Python", 42]  # element_type "string"

    errors = validate(profile, _config())

    assert any(e.field == "skills[1]" and "element type" in e.reason for e in errors)


def test_missing_object_subfield_is_reported() -> None:
    """A declared object subfield that is absent fails the object check (Req 9.7)."""
    profile = _conforming_profile()
    profile["location"] = {"city": "Seattle"}  # missing "country"

    errors = validate(profile, _config())

    assert any(e.field == "location.country" and "missing" in e.reason for e in errors)


def test_mistyped_object_subfield_is_reported() -> None:
    """A declared object subfield of the wrong type fails the object check (Req 9.7)."""
    profile = _conforming_profile()
    profile["location"] = {"city": 99, "country": "US"}  # city should be string

    errors = validate(profile, _config())

    assert any(e.field == "location.city" and "type" in e.reason for e in errors)


def test_mistyped_subfield_in_array_of_objects_is_reported() -> None:
    """A wrong-typed subfield inside an array-of-objects element is named with path."""
    profile = _conforming_profile()
    profile["experience"] = [{"company": "Acme", "title": 5}]  # title should be string

    errors = validate(profile, _config())

    assert any(
        e.field == "experience[0].title" and "type" in e.reason for e in errors
    )


def test_present_null_value_is_accepted() -> None:
    """A present null value satisfies presence and skips structural checks."""
    profile = _conforming_profile()
    profile["country"] = None  # honestly-empty, has an enum + type declared

    errors = validate(profile, _config())

    assert errors == []


def test_validation_error_converts_to_error_report() -> None:
    """A ValidationError maps to an ErrorReport tagged stage='validate' (Req 11.1)."""
    err = ValidationError(field="id", reason="required field is missing")
    report = err.to_error_report()

    assert report.stage == "validate"
    assert "id" in report.error


def test_validator_class_matches_module_function() -> None:
    """The Validator class and the module-level wrapper agree."""
    profile = _conforming_profile()
    profile["id"] = 1
    config = _config()

    assert Validator().validate(profile, config) == validate(profile, config)

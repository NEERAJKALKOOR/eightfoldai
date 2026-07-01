"""Property-based test for output schema validation soundness.

Feature: candidate-data-transformer, Property 16

**Property 16: Schema validation soundness** -- *For any* schema and a
``Projected_Profile`` generated to conform to it, validation passes; and for any
single injected violation (wrong type, missing required field, out-of-enum value,
non-list for an array field, or a missing/mistyped object subfield), validation
fails with an error identifying the offending field and reason.

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7**

The schema is a :class:`~candidate_transformer.engine.projection.ProjectionConfig`
whose field entries declare ``type`` / ``required`` / ``enum`` / ``element_type``;
the conforming ``Projected_Profile`` is built (in lock-step with the schema) to
satisfy every declared check. Each iteration confirms the conforming profile
validates cleanly, then injects exactly one violation of each kind into an
otherwise-conforming copy and asserts a :class:`ValidationError` names the offending
field.
"""

from __future__ import annotations

import copy
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from candidate_transformer.engine.projection import ProjectionConfig
from candidate_transformer.engine.validation import validate

# Scalar declared types the validator models (Req 9.3). ``boolean`` is excluded from
# enum fields below so a distinct out-of-enum value is always generable.
_SCALAR_TYPES = ["string", "number", "boolean"]

# For an injected "wrong type" violation: a value that is NOT an instance of the
# declared type (and is non-null, since a present null is accepted as honestly-empty).
_WRONG_VALUE: dict[str, Any] = {
    "string": 123,  # an int is not a string
    "number": "not-a-number",  # a str is not a number
    "boolean": "not-a-bool",  # a str is not a boolean
    "array": "not-a-list",  # a str is not an array
    "object": "not-an-object",  # a str is not an object
}


def _scalar_strategy(type_name: str) -> st.SearchStrategy[Any]:
    """A Hypothesis strategy producing valid values for a declared scalar type."""
    if type_name == "string":
        return st.text(max_size=8)
    if type_name == "number":
        return st.one_of(
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
        )
    if type_name == "boolean":
        return st.booleans()
    raise AssertionError(f"unhandled scalar type {type_name!r}")


# Object-structure subfield names: short, unique identifiers.
_subfield_names = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4),
    min_size=1,
    max_size=3,
    unique=True,
)


@st.composite
def _object_structure(draw: Any) -> dict[str, str]:
    """Draw a ``{subfield: scalar_type}`` object-structure declaration."""
    names = draw(_subfield_names)
    return {name: draw(st.sampled_from(_SCALAR_TYPES)) for name in names}


@st.composite
def _schema_and_profile(draw: Any) -> dict[str, Any]:
    """Draw an inline schema plus a conforming profile and per-violation injections.

    The schema always contains one field exercising each validation check so every
    violation kind has a concrete target each iteration:

    * ``s_req``      -- a required scalar field (wrong-type, missing-required)
    * ``e_enum``     -- a scalar field with an ``enum`` (out-of-enum)
    * ``arr_scalar`` -- an ``array`` of scalars (non-list)
    * ``arr_obj``    -- an ``array`` of objects (mistyped element subfield)
    * ``obj``        -- an ``object`` field (missing subfield)
    """
    # Required scalar field -----------------------------------------------------
    req_type = draw(st.sampled_from(_SCALAR_TYPES))
    req_value = draw(_scalar_strategy(req_type))

    # Enum scalar field (string/number so an out-of-enum value always exists) ----
    enum_type = draw(st.sampled_from(["string", "number"]))
    enum_values = draw(
        st.lists(_scalar_strategy(enum_type), min_size=2, max_size=4, unique=True)
    )
    enum_value = draw(st.sampled_from(enum_values))
    out_of_enum_value = draw(
        _scalar_strategy(enum_type).filter(lambda v: v not in enum_values)
    )

    # Array of scalars ----------------------------------------------------------
    elem_type = draw(st.sampled_from(_SCALAR_TYPES))
    arr_scalar = draw(st.lists(_scalar_strategy(elem_type), max_size=4))

    # Array of objects ----------------------------------------------------------
    arr_obj_struct = draw(_object_structure())
    arr_obj_len = draw(st.integers(min_value=1, max_value=3))
    arr_obj = [
        {sf: draw(_scalar_strategy(t)) for sf, t in arr_obj_struct.items()}
        for _ in range(arr_obj_len)
    ]
    arr_obj_subfield = draw(st.sampled_from(list(arr_obj_struct)))
    arr_obj_wrong_value = _WRONG_VALUE[arr_obj_struct[arr_obj_subfield]]

    # Object field --------------------------------------------------------------
    obj_struct = draw(_object_structure())
    obj_value = {sf: draw(_scalar_strategy(t)) for sf, t in obj_struct.items()}
    obj_missing_subfield = draw(st.sampled_from(list(obj_struct)))

    config = ProjectionConfig.from_dict(
        {
            "include_provenance": False,
            "include_confidence": False,
            "fields": [
                {"name": "s_req", "from": "s_req", "type": req_type, "required": True},
                {"name": "e_enum", "from": "e_enum", "type": enum_type, "enum": enum_values},
                {
                    "name": "arr_scalar",
                    "from": "arr_scalar",
                    "type": "array",
                    "element_type": elem_type,
                },
                {
                    "name": "arr_obj",
                    "from": "arr_obj",
                    "type": "array",
                    "element_type": arr_obj_struct,
                },
                {
                    "name": "obj",
                    "from": "obj",
                    "type": "object",
                    "element_type": obj_struct,
                },
            ],
        }
    )

    profile = {
        "s_req": req_value,
        "e_enum": enum_value,
        "arr_scalar": arr_scalar,
        "arr_obj": arr_obj,
        "obj": obj_value,
    }

    return {
        "config": config,
        "profile": profile,
        "wrong_type_value": _WRONG_VALUE[req_type],
        "out_of_enum_value": out_of_enum_value,
        "non_list_value": "not-a-list",
        "obj_missing_subfield": obj_missing_subfield,
        "arr_obj_subfield": arr_obj_subfield,
        "arr_obj_wrong_value": arr_obj_wrong_value,
    }


@given(data=_schema_and_profile())
def test_schema_validation_soundness(data: dict[str, Any]) -> None:
    """Conforming profiles pass; each single injected violation is reported.

    Feature: candidate-data-transformer, Property 16
    Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7
    """
    config = data["config"]
    profile = data["profile"]

    # Soundness on conforming input: a profile built to satisfy the schema validates
    # with no errors (Req 9.1).
    assert validate(profile, config) == []

    # Wrong type for a scalar field -> type error naming the field (Req 9.3).
    p = copy.deepcopy(profile)
    p["s_req"] = data["wrong_type_value"]
    errors = validate(p, config)
    assert any(e.field == "s_req" and "type" in e.reason for e in errors)

    # Missing required field -> presence error naming the field (Req 9.4).
    p = copy.deepcopy(profile)
    del p["s_req"]
    errors = validate(p, config)
    assert any(e.field == "s_req" and "missing" in e.reason for e in errors)

    # Out-of-enum value -> enum error naming the field (Req 9.5).
    p = copy.deepcopy(profile)
    p["e_enum"] = data["out_of_enum_value"]
    errors = validate(p, config)
    assert any(e.field == "e_enum" and "allowed set" in e.reason for e in errors)

    # Non-list for an array field -> type error naming the field (Req 9.6).
    p = copy.deepcopy(profile)
    p["arr_scalar"] = data["non_list_value"]
    errors = validate(p, config)
    assert any(e.field == "arr_scalar" and "type" in e.reason for e in errors)

    # Missing object subfield -> error naming the nested subfield path (Req 9.7).
    p = copy.deepcopy(profile)
    missing_sub = data["obj_missing_subfield"]
    del p["obj"][missing_sub]
    errors = validate(p, config)
    assert any(
        e.field == f"obj.{missing_sub}" and "missing" in e.reason for e in errors
    )

    # Mistyped subfield inside an array-of-objects element -> error naming the
    # nested element path (Req 9.6, 9.7).
    p = copy.deepcopy(profile)
    arr_sub = data["arr_obj_subfield"]
    p["arr_obj"][0][arr_sub] = data["arr_obj_wrong_value"]
    errors = validate(p, config)
    assert any(
        e.field == f"arr_obj[0].{arr_sub}" and "type" in e.reason for e in errors
    )

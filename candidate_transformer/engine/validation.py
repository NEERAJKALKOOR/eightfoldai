"""Output schema validation (Req 9).

The :class:`Validator` (the ``Validation_Module``) checks a ``Projected_Profile``
produced by the :class:`~candidate_transformer.engine.projection.ProjectionEngine`
against the schema declared inline in the ``Projection_Config`` field entries. It is
**decoupled from projection**: projection emits the profile and reports projection
errors (``stage="project"``); validation is a separate pass that consumes the same
:class:`~candidate_transformer.engine.projection.ProjectionConfig` and reports
*validation* errors naming the failing field and the reason (Req 9.1, 9.2).

The inline schema lives on each
:class:`~candidate_transformer.engine.projection.FieldSpec`:

* ``type`` -- the declared scalar/array/object type (Req 9.3).
* ``required`` -- whether the field must be present in the profile (Req 9.4).
* ``enum`` -- the set of allowed values (Req 9.5).
* ``element_type`` -- for an ``array`` field, the element type; either a scalar type
  name (array of scalars, Req 9.6) or a ``{subfield: type}`` mapping (array of
  objects). For an ``object`` field, the ``{subfield: type}`` mapping declaring the
  object's structure (Req 9.7).

Checks performed per field (in order):

1. **required / presence** -- a field declared ``required`` must be present (Req 9.4).
2. **type** -- the value matches the declared scalar/array/object type (Req 9.3).
3. **enum** -- the value is a member of the declared set (Req 9.5).
4. **array** -- an ``array`` value is a list and every element matches the declared
   ``element_type`` (Req 9.6).
5. **object** -- an ``object`` value contains the declared subfields with their
   declared types (Req 9.7).

Honestly-empty handling: a field that is present with a ``null`` value (e.g. emitted
by ``on_missing="null"``) satisfies the required *presence* check, and its ``null``
value is treated as honestly-empty -- the type/enum/array/object checks are skipped
for it rather than reported as a type mismatch. This mirrors the system principle
that an honest null is acceptable where an invented value would not be.

Each failure is reported as a structured :class:`ValidationError` (``{field, reason}``)
that names the offending field (including a nested path such as ``experience[0].company``
for array/object subfields) and the human-readable reason (Req 9.2). A
:class:`ValidationError` can be converted to the codebase's standard
:class:`~candidate_transformer.models.reporting.ErrorReport` with ``stage="validate"``
via :meth:`ValidationError.to_error_report`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..models.reporting import ErrorReport
from .projection import ProjectionConfig

__all__ = [
    "ValidationError",
    "Validator",
    "validate",
]

# Declared-type-name -> predicate confirming a value matches that type (Req 9.3).
# ``number`` excludes ``bool`` because ``bool`` is a subclass of ``int`` in Python
# but is not a JSON number for schema purposes.
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


@dataclass(frozen=True)
class ValidationError:
    """A structured validation failure of shape ``{ field, reason }`` (Req 9.2).

    ``field`` names the offending output field -- a nested path such as
    ``experience[0].company`` when the failure is in an array element or object
    subfield. ``reason`` is a human-readable description of why validation failed.
    """

    field: str
    reason: str

    def to_error_report(self) -> ErrorReport:
        """Convert this validation error to a standard :class:`ErrorReport`.

        The report is tagged ``stage="validate"`` and its message includes the
        failing field name and reason, so validation failures flow through the same
        structured-error channel as the rest of the pipeline (Req 11.1, 11.2).
        """
        return ErrorReport(
            source=None,
            stage="validate",
            error=f"field {self.field!r}: {self.reason}",
        )


def _type_name(value: Any) -> str:
    """Return a friendly JSON-ish type name for ``value`` for error messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, declared_type: str) -> bool:
    """Return True if ``value`` matches ``declared_type``.

    Supports both ``"array"`` and the ``"<scalar>[]"`` shorthand (e.g. ``"string[]"``
    from the assignment's example config), which is treated as an array. Unknown
    declared type names are treated permissively (no failure) so that the validator
    never rejects a profile because of a type name it does not model.
    """
    if isinstance(declared_type, str) and declared_type.endswith("[]"):
        return isinstance(value, list)
    check = _TYPE_CHECKS.get(declared_type)
    if check is None:
        return True
    return check(value)


def _array_element_type(spec_type: str | None, element_type: Any) -> Any:
    """Resolve the element type for an array field.

    Prefers an explicit ``element_type``; otherwise derives it from a
    ``"<scalar>[]"`` shorthand (e.g. ``"string[]"`` -> ``"string"``).
    """
    if element_type is not None:
        return element_type
    if isinstance(spec_type, str) and spec_type.endswith("[]"):
        return spec_type[:-2] or None
    return None


def _is_array_type(declared_type: str | None) -> bool:
    """True when the declared type denotes an array (``"array"`` or ``"<x>[]"``)."""
    return declared_type == "array" or (
        isinstance(declared_type, str) and declared_type.endswith("[]")
    )


class Validator:
    """Validates a ``Projected_Profile`` against a ``Projection_Config`` schema.

    Stateless: :meth:`validate` is a pure function of its ``(profile, config)``
    inputs and never mutates either argument.
    """

    def validate(
        self, profile: dict[str, Any], config: ProjectionConfig
    ) -> list[ValidationError]:
        """Validate ``profile`` against the inline schema declared in ``config``.

        Returns a list of :class:`ValidationError`s -- empty when the profile
        conforms to every field's declared schema (Req 9.1). Each error names the
        failing field and the reason (Req 9.2).
        """
        errors: list[ValidationError] = []

        for spec in config.fields:
            present = spec.name in profile

            # 1. required / presence check (Req 9.4).
            if not present:
                if spec.required:
                    errors.append(
                        ValidationError(
                            field=spec.name,
                            reason="required field is missing from the projected profile",
                        )
                    )
                # A non-required absent field has nothing further to validate.
                continue

            value = profile[spec.name]

            # Honestly-empty: a present null value is accepted and skips the
            # structural checks (it is an honest absence, not a type violation).
            if value is None:
                continue

            # 2. type check (Req 9.3).
            if spec.type is not None and not _matches_type(value, spec.type):
                errors.append(
                    ValidationError(
                        field=spec.name,
                        reason=(
                            f"expected type {spec.type!r} but got "
                            f"{_type_name(value)!r}"
                        ),
                    )
                )
                # Type is wrong; deeper checks would be misleading.
                continue

            # 3. enum check (Req 9.5).
            if spec.enum is not None and value not in spec.enum:
                errors.append(
                    ValidationError(
                        field=spec.name,
                        reason=(
                            f"value {value!r} is not a member of the allowed set "
                            f"{list(spec.enum)!r}"
                        ),
                    )
                )

            # 4. array check (Req 9.6) -- supports "array" and "<scalar>[]" shorthand.
            if _is_array_type(spec.type):
                element_type = _array_element_type(spec.type, spec.element_type)
                errors.extend(
                    self._validate_array(spec.name, value, element_type)
                )

            # 5. object check (Req 9.7).
            elif spec.type == "object":
                errors.extend(
                    self._validate_object(spec.name, value, spec.element_type)
                )

        return errors

    def _validate_array(
        self, field_name: str, value: Any, element_type: Any
    ) -> list[ValidationError]:
        """Validate each element of an ``array`` field against ``element_type``.

        ``element_type`` may be a scalar type name (array of scalars) or a
        ``{subfield: type}`` mapping (array of objects). When ``element_type`` is not
        declared, only the list-ness of the value matters (already confirmed by the
        type check), so no element checks are performed. (Req 9.6)
        """
        # The list-ness was already confirmed by the type check; guard defensively.
        if not isinstance(value, list) or element_type is None:
            return []

        errors: list[ValidationError] = []
        for index, element in enumerate(value):
            element_field = f"{field_name}[{index}]"
            if isinstance(element_type, dict):
                # Array of objects: each element must satisfy the declared structure.
                errors.extend(
                    self._validate_object(element_field, element, element_type)
                )
            elif isinstance(element_type, str):
                # Array of scalars: each element must match the scalar type name.
                if element is not None and not _matches_type(element, element_type):
                    errors.append(
                        ValidationError(
                            field=element_field,
                            reason=(
                                f"expected element type {element_type!r} but got "
                                f"{_type_name(element)!r}"
                            ),
                        )
                    )
        return errors

    def _validate_object(
        self, field_name: str, value: Any, structure: Any
    ) -> list[ValidationError]:
        """Validate that ``value`` is an object with the declared subfields/types.

        ``structure`` is a ``{subfield: type}`` mapping. Each declared subfield must
        be present in ``value`` and match its declared type. (Req 9.7)
        """
        if not isinstance(value, dict):
            return [
                ValidationError(
                    field=field_name,
                    reason=(
                        f"expected an object but got {_type_name(value)!r}"
                    ),
                )
            ]

        if not isinstance(structure, dict):
            # No declared structure to enforce.
            return []

        errors: list[ValidationError] = []
        for subfield, sub_type in structure.items():
            sub_field_name = f"{field_name}.{subfield}"
            if subfield not in value:
                errors.append(
                    ValidationError(
                        field=sub_field_name,
                        reason="required object subfield is missing",
                    )
                )
                continue
            sub_value = value[subfield]
            # An honestly-empty subfield value is accepted.
            if sub_value is None:
                continue
            if isinstance(sub_type, str) and not _matches_type(sub_value, sub_type):
                errors.append(
                    ValidationError(
                        field=sub_field_name,
                        reason=(
                            f"expected type {sub_type!r} but got "
                            f"{_type_name(sub_value)!r}"
                        ),
                    )
                )
            elif isinstance(sub_type, dict):
                # Nested object structure.
                errors.extend(
                    self._validate_object(sub_field_name, sub_value, sub_type)
                )
        return errors


def validate(
    profile: dict[str, Any], config: ProjectionConfig
) -> list[ValidationError]:
    """Module-level convenience wrapper over :meth:`Validator.validate`."""
    return Validator().validate(profile, config)

"""Config-driven projection engine (Req 8, 15).

The :class:`ProjectionEngine` interprets a ``Projection_Config`` at runtime and
reshapes a :class:`~candidate_transformer.models.canonical.CanonicalRecord` into a
caller-specified ``Projected_Profile``. It is a **read-only** transformation: every
output value is read out of the canonical record via the path resolver
(:func:`~candidate_transformer.engine.path_resolver.resolve_path`) and written into
a *fresh* output object, so the canonical record is never mutated and one record can
safely drive many projections (Req 8.2, 15.1, 15.2, 15.3).

Config shape (matching ``samples/configs/*.json`` and the design example)::

    {
      "include_provenance": false,
      "include_confidence": true,
      "fields": [
        { "name": "id", "from": "candidate_id", "type": "string",
          "required": true, "on_missing": "error" },
        { "name": "skills", "from": "skills[].name", "type": "array",
          "element_type": "string", "on_missing": "null" },
        ...
      ]
    }

Per-field capabilities handled here (Req 8.1, 8.3, 8.4, 8.8-8.16):

* **field selection** -- only the configured fields appear in the output (Req 8.3).
* **rename / remap** -- the value read from ``from`` is written under ``name``
  (Req 8.4, 8.8).
* **per-field normalize** -- ``"lowercase"`` / ``"uppercase"`` transforms applied to
  the projected value (Req 8.10).
* **on_missing** -- when the ``from`` path resolves to an absent field, ``"null"``
  emits ``null`` (Req 8.13), ``"omit"`` drops the field (Req 8.14), and ``"error"``
  reports a projection error naming the field (Req 8.15).
* **invalid path** -- an out-of-range index or non-existent subfield (surfaced as
  :class:`InvalidPathError` by the resolver) reports a projection error naming the
  invalid path (Req 8.16).
* **provenance / confidence toggles** -- ``include_provenance`` / ``include_confidence``,
  when off, omit provenance / confidence from the output (Req 8.11, 8.12).

The nested ``type`` / ``enum`` / ``element_type`` / ``required`` declarations are
*parsed and retained* on the :class:`FieldSpec` so the separate Validation_Module
(task 12.1) can consume them; the deeper structural type/enum checks are **not**
performed here. The only schema-ish behavior this engine performs is the
``required`` + ``on_missing="error"`` reporting for absent fields (Req 8.15).

All behavior is deterministic: identical record + config produce identical output,
with output keys in config order followed by the optional provenance/confidence keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..models.canonical import CanonicalRecord
from ..models.reporting import ErrorReport
from ..models.serialization import to_dict
from ..normalizers import normalize_phone, normalize_skill
from .path_resolver import MISSING, InvalidPathError, resolve_path

__all__ = [
    "ProjectionConfigError",
    "FieldSpec",
    "ProjectionConfig",
    "ProjectionEngine",
    "project",
]

# Accepted per-field missing-value behaviors (Req 8.13, 8.14, 8.15).
_ON_MISSING_CHOICES = frozenset({"null", "omit", "error"})


def _norm_e164(value: str) -> str:
    """Project-time E.164 normalization for a phone string (Req 8.10).

    Canonical phones are already E.164, so this is normally idempotent; if the value
    cannot be reparsed it is returned unchanged rather than nulled (projection never
    invents or destroys a present value).
    """
    normalized, _quality = normalize_phone(value)
    return normalized if normalized is not None else value


def _norm_canonical(value: str) -> str:
    """Project-time canonical-skill normalization for a string (Req 8.10).

    Maps the value to its Canonical_Skill_Name; unknown values are returned
    unchanged (the canonical record already holds canonical names, so this is
    normally idempotent).
    """
    normalized, _quality = normalize_skill(value)
    return normalized if normalized is not None else value


# Accepted per-field normalization transforms (Req 8.10). Keys are matched
# case-insensitively, so the assignment's ``"E164"`` and our ``"e164"`` both work.
_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "lowercase": str.lower,
    "uppercase": str.upper,
    "e164": _norm_e164,
    "canonical": _norm_canonical,
}


class ProjectionConfigError(ValueError):
    """Raised when a ``Projection_Config`` cannot be parsed into a config object.

    Signals a structurally invalid config (e.g. a field entry missing ``name`` or
    ``from``, or an unknown ``on_missing`` / ``normalize`` value). The CLI surfaces
    this as a configuration error (Req 13.5); the projection engine itself never
    raises it at projection time.
    """


@dataclass(frozen=True)
class FieldSpec:
    """A single parsed ``Projection_Config`` field entry.

    ``name`` is the output field name and ``from_`` is the canonical ``from`` path.
    ``type``, ``enum``, ``element_type``, and ``required`` are retained verbatim for
    the Validation_Module (task 12.1) and are not structurally enforced here.
    ``normalize`` and ``on_missing`` drive this engine's per-field behavior.
    """

    name: str
    from_: str
    type: str | None = None
    required: bool = False
    enum: list[Any] | None = None
    normalize: str | None = None
    element_type: Any = None
    on_missing: str = "null"

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, default_on_missing: str = "null"
    ) -> "FieldSpec":
        """Parse one field entry dict into a :class:`FieldSpec`.

        Accepts both config dialects:

        * **ours** -- ``"name"`` is the output field, ``"from"`` is the canonical
          source path.
        * **the assignment example** -- ``"path"`` is the output field; when no
          ``"from"`` is given the same ``"path"`` is also used as the source path
          (e.g. ``{"path": "full_name"}`` reads ``full_name`` and outputs it as
          ``full_name``).

        ``on_missing`` falls back to ``default_on_missing`` (the config-level global)
        when not set per field. ``normalize`` is matched case-insensitively, so the
        assignment's ``"E164"`` and ``"canonical"`` work alongside
        ``"lowercase"``/``"uppercase"``.

        Raises :class:`ProjectionConfigError` when the output name is absent or a
        ``normalize`` / ``on_missing`` value is not recognized.
        """
        if not isinstance(data, dict):
            raise ProjectionConfigError(
                f"field entry must be an object, got {type(data).__name__}"
            )
        # Output field name: "name" (ours) or "path" (assignment example).
        name = data.get("name") or data.get("path")
        if not isinstance(name, str) or not name:
            raise ProjectionConfigError(
                "field entry is missing a non-empty 'name' (or 'path')"
            )
        # Source canonical path: explicit "from", else fall back to the output name
        # (so {"path": "full_name"} reads and writes full_name).
        from_ = data.get("from") or name
        if not isinstance(from_, str) or not from_:
            raise ProjectionConfigError(
                f"field {name!r} is missing a non-empty 'from' path"
            )

        on_missing = data.get("on_missing", default_on_missing)
        if on_missing not in _ON_MISSING_CHOICES:
            raise ProjectionConfigError(
                f"field {name!r} has invalid on_missing {on_missing!r}; "
                f"expected one of {sorted(_ON_MISSING_CHOICES)}"
            )

        raw_normalize = data.get("normalize")
        normalize = raw_normalize.lower() if isinstance(raw_normalize, str) else None
        if raw_normalize is not None and normalize not in _NORMALIZERS:
            raise ProjectionConfigError(
                f"field {name!r} has unsupported normalize {raw_normalize!r}; "
                f"expected one of {sorted(_NORMALIZERS)}"
            )

        return cls(
            name=name,
            from_=from_,
            type=data.get("type"),
            required=bool(data.get("required", False)),
            enum=data.get("enum"),
            normalize=normalize,
            element_type=data.get("element_type"),
            on_missing=on_missing,
        )


@dataclass
class ProjectionConfig:
    """A parsed ``Projection_Config`` document.

    ``fields`` is the ordered list of :class:`FieldSpec`s. ``include_provenance`` and
    ``include_confidence`` are the top-level output toggles (Req 8.11, 8.12); both
    default to ``True`` (included) and are turned off explicitly in a config.
    """

    fields: list[FieldSpec] = field(default_factory=list)
    include_provenance: bool = True
    include_confidence: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectionConfig":
        """Parse a ``Projection_Config`` dict (e.g. loaded JSON) into a config object.

        Supports a top-level ``"on_missing"`` default (the assignment example sets
        one globally); each field's own ``on_missing`` overrides it. Raises
        :class:`ProjectionConfigError` when the document or any field entry is
        structurally invalid.
        """
        if not isinstance(data, dict):
            raise ProjectionConfigError(
                f"projection config must be an object, got {type(data).__name__}"
            )
        raw_fields = data.get("fields", [])
        if not isinstance(raw_fields, list):
            raise ProjectionConfigError("'fields' must be a list of field entries")

        default_on_missing = data.get("on_missing", "null")
        if default_on_missing not in _ON_MISSING_CHOICES:
            raise ProjectionConfigError(
                f"top-level on_missing {default_on_missing!r} is invalid; "
                f"expected one of {sorted(_ON_MISSING_CHOICES)}"
            )

        fields = [
            FieldSpec.from_dict(entry, default_on_missing=default_on_missing)
            for entry in raw_fields
        ]
        return cls(
            fields=fields,
            include_provenance=bool(data.get("include_provenance", True)),
            include_confidence=bool(data.get("include_confidence", True)),
        )


def _apply_normalize(value: Any, transform: str) -> Any:
    """Apply a per-field ``normalize`` transform to ``value`` (Req 8.10).

    Strings are transformed directly; for list values each string element is
    transformed (non-string elements are left unchanged) so array projections such
    as ``skills[].name`` honor a declared normalization. Non-string scalars pass
    through unchanged. ``transform`` is a normalized (lowercased) registry key.
    """
    func = _NORMALIZERS[transform]
    if isinstance(value, str):
        return func(value)
    if isinstance(value, list):
        return [func(item) if isinstance(item, str) else item for item in value]
    return value


class ProjectionEngine:
    """Runtime, config-driven projection of a canonical record (Req 8, 15).

    Stateless: :meth:`project` is a pure function of its ``(record, config)`` inputs
    and never mutates the record.
    """

    def project(
        self, record: CanonicalRecord, config: ProjectionConfig
    ) -> tuple[dict[str, Any], list[ErrorReport]]:
        """Project ``record`` through ``config`` into ``(profile, errors)``.

        Builds a fresh output dict containing exactly the configured fields (subject
        to ``on_missing``), reading each value from its ``from`` path. Applies any
        per-field ``normalize`` and honors the ``include_provenance`` /
        ``include_confidence`` toggles. Returns the projected profile and a list of
        projection :class:`ErrorReport`s (``stage="project"``) for absent required
        fields (Req 8.15) and invalid paths (Req 8.16). The canonical record is left
        unchanged (Req 8.2, 15.1, 15.3).

        Parameters
        ----------
        record:
            The canonical record to read from (never modified).
        config:
            The parsed projection configuration.

        Returns
        -------
        tuple[dict, list[ErrorReport]]
            The projected profile dict (keys in config order, then the optional
            ``provenance`` / ``overall_confidence`` keys) and the projection errors.
        """
        profile: dict[str, Any] = {}
        errors: list[ErrorReport] = []

        for spec in config.fields:
            try:
                resolved = resolve_path(record, spec.from_)
            except InvalidPathError as exc:
                # Out-of-range index or non-existent subfield -> invalid path error
                # naming the path (Req 8.16); the field is excluded from the output.
                errors.append(
                    ErrorReport(
                        source=None,
                        stage="project",
                        error=(
                            f"invalid canonical path {exc.path!r} for output field "
                            f"{spec.name!r}"
                            + (f": {exc.reason}" if exc.reason else "")
                        ),
                    )
                )
                continue

            if resolved is MISSING:
                # The canonical field is absent -> apply the on_missing behavior.
                if spec.on_missing == "omit":
                    continue  # Req 8.14: drop the field entirely.
                if spec.on_missing == "error":
                    # Req 8.15: report a projection error naming the field.
                    errors.append(
                        ErrorReport(
                            source=None,
                            stage="project",
                            error=(
                                f"required output field {spec.name!r} is absent from "
                                f"the canonical record (from {spec.from_!r})"
                            ),
                        )
                    )
                    continue
                # Req 8.13: on_missing == "null" -> emit an explicit null.
                profile[spec.name] = None
                continue

            # Serialize dataclass/nested values into JSON-ready structures, then
            # apply any per-field normalization (Req 8.10).
            value = to_dict(resolved)
            if spec.normalize is not None:
                value = _apply_normalize(value, spec.normalize)
            profile[spec.name] = value

        # Provenance toggle (Req 8.11): include the record's provenance entries when
        # on, omit them when off.
        if config.include_provenance:
            profile["provenance"] = to_dict(record.provenance)

        # Confidence toggle (Req 8.12): include the overall confidence when on, omit
        # it when off.
        if config.include_confidence:
            profile["overall_confidence"] = record.overall_confidence

        return profile, errors


def project(
    record: CanonicalRecord, config: ProjectionConfig
) -> tuple[dict[str, Any], list[ErrorReport]]:
    """Module-level convenience wrapper over :meth:`ProjectionEngine.project`."""
    return ProjectionEngine().project(record, config)

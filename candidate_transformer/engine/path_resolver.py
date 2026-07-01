"""Canonical path grammar parser and resolver (Req 8.5, 8.6, 8.7).

This module is the foundation of the Projection_Module's path-resolution engine
(task 11.1). The config-driven projection layer (task 11.2) consumes the parser
and resolver defined here to read values out of a ``CanonicalRecord`` at the
``from`` paths declared in a ``Projection_Config`` -- it does **not** apply the
projection policy (``on_missing`` / errors / normalization) itself.

Canonical path grammar (from the design)::

    path     := segment ('.' segment)*
    segment  := name | name '[' index ']' | name '[]' ('.' name)?

Three kinds of segment are supported:

* **nested** -- ``location.city`` resolves attribute/key access through nested
  dataclasses and dicts (Req 8.5).
* **indexed** -- ``phones[0]`` reads the element at a fixed list index (Req 8.6).
* **array projection** -- ``skills[].name`` produces a list containing the named
  subfield from every element of a list-valued field (Req 8.7). The optional
  trailing ``.name`` selects a subfield; ``skills[]`` alone yields the elements
  themselves. An array projection must be the final segment of a path.

Resolution distinguishes three outcomes clearly so the projection layer can apply
the right policy (Req 8.16):

* **field absent** -- a named field/key does not exist on a dict, or a resolved
  value is ``None``, or an indexed access targets an empty list. Signalled by the
  :data:`MISSING` sentinel so the caller can apply its ``on_missing`` behavior.
* **out-of-range index** -- an index points beyond a non-empty list (e.g.
  ``phones[50]``). Signalled by raising :class:`InvalidPathError`.
* **non-existent subfield** -- an array-projection subfield does not exist on the
  elements (e.g. ``skills[].abc``), or a nested segment names a field that does
  not exist on a dataclass. Signalled by raising :class:`InvalidPathError`.

All functions are pure and deterministic: identical inputs always produce identical
output, with no wall-clock or randomness, and the resolver never mutates the record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, is_dataclass
from typing import Any

__all__ = [
    "MISSING",
    "InvalidPathError",
    "FieldSegment",
    "IndexSegment",
    "ProjectionSegment",
    "Segment",
    "parse_path",
    "resolve_path",
]


class _Missing:
    """Singleton sentinel marking an absent field (distinct from a present ``None``).

    The projection layer applies its ``on_missing`` behavior when a resolution
    returns :data:`MISSING`. It is falsy and reprs as ``MISSING`` for readable
    debugging output.
    """

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "MISSING"

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return False


#: Sentinel returned when a path resolves to an absent field (Req 8.9 support).
MISSING = _Missing()


class InvalidPathError(Exception):
    """Raised when a canonical path is structurally invalid or cannot be resolved.

    Carries the offending ``path`` so the projection layer can report a projection
    error identifying the invalid path (Req 8.16). The two resolution-time triggers
    are an out-of-range array index (e.g. ``phones[50]``) and a non-existent
    subfield (e.g. ``skills[].abc``); the parser also raises it for paths that do
    not conform to the grammar.
    """

    def __init__(self, path: str, reason: str | None = None) -> None:
        self.path = path
        self.reason = reason
        message = f"invalid canonical path: {path!r}"
        if reason:
            message += f" ({reason})"
        super().__init__(message)


@dataclass(frozen=True)
class FieldSegment:
    """A plain nested segment -- attribute/key access by ``name`` (e.g. ``city``)."""

    name: str


@dataclass(frozen=True)
class IndexSegment:
    """An indexed segment -- element ``index`` of the list ``name`` (e.g. ``phones[0]``)."""

    name: str
    index: int


@dataclass(frozen=True)
class ProjectionSegment:
    """An array-projection segment over list ``name`` (e.g. ``skills[].name``).

    ``subfield`` is the named subfield read from each element, or ``None`` for a
    bare ``skills[]`` projection that yields the elements themselves. A projection
    is always the terminal segment of a path.
    """

    name: str
    subfield: str | None = None


# A parsed path segment is one of the three segment kinds.
Segment = FieldSegment | IndexSegment | ProjectionSegment

# name | name '[' index ']' | name '[]'
_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\]|(\[\]))?$")
# a bare name, used to validate an array-projection subfield token
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_path(path: str) -> list[Segment]:
    """Parse a canonical path string into an ordered list of segments.

    Implements the canonical path grammar. The dot-separated tokens are classified
    into :class:`FieldSegment`, :class:`IndexSegment`, or :class:`ProjectionSegment`.
    For an array projection (``name[]``) the immediately following dotted name, when
    present, is consumed as the projection ``subfield`` (so ``skills[].name`` is a
    single :class:`ProjectionSegment`). An array projection must be the final
    component of the path.

    Parameters
    ----------
    path:
        The canonical path string, e.g. ``"location.city"``, ``"phones[0]"``, or
        ``"skills[].name"``.

    Returns
    -------
    list[Segment]
        The parsed segments in left-to-right order.

    Raises
    ------
    InvalidPathError
        If ``path`` is empty or any token does not conform to the grammar, or if an
        array projection is not the final segment.
    """
    if not isinstance(path, str) or not path.strip():
        raise InvalidPathError(str(path), "empty path")

    tokens = path.split(".")
    segments: list[Segment] = []
    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        match = _SEGMENT_RE.match(token)
        if match is None:
            raise InvalidPathError(path, f"invalid segment {token!r}")
        name, index, projection = match.group(1), match.group(2), match.group(3)

        if projection is not None:
            # name[] -- an array projection; optionally consume a trailing subfield.
            subfield: str | None = None
            if i + 1 < n:
                next_token = tokens[i + 1]
                if not _NAME_RE.match(next_token):
                    raise InvalidPathError(
                        path, f"invalid array-projection subfield {next_token!r}"
                    )
                subfield = next_token
                i += 1
            if i + 1 < n:
                raise InvalidPathError(
                    path, "array projection must be the final segment"
                )
            segments.append(ProjectionSegment(name, subfield))
        elif index is not None:
            segments.append(IndexSegment(name, int(index)))
        else:
            segments.append(FieldSegment(name))
        i += 1

    return segments


def _get_attr(container: Any, name: str, path: str) -> Any:
    """Read field ``name`` from ``container`` (a dataclass, dict, or ``None``).

    Returns :data:`MISSING` when the field is absent: the container is ``None`` (a
    null parent), or the key is absent from a dict. For a dataclass, a name that is
    not an attribute of the instance is a non-existent subfield and raises
    :class:`InvalidPathError`. Reading a named field from a non-container scalar
    (e.g. ``str``/``int``) is also an invalid path.
    """
    if container is None or container is MISSING:
        return MISSING
    if is_dataclass(container) and not isinstance(container, type):
        if hasattr(container, name):
            return getattr(container, name)
        raise InvalidPathError(path, f"non-existent subfield {name!r}")
    if isinstance(container, dict):
        return container.get(name, MISSING)
    # A scalar / list cannot be navigated by a named segment.
    raise InvalidPathError(
        path, f"cannot read field {name!r} from {type(container).__name__}"
    )


def _get_subfield_for_projection(element: Any, subfield: str, path: str) -> Any:
    """Read ``subfield`` from a single projection ``element``.

    The subfield must exist on every element; a missing attribute/key (e.g.
    ``skills[].abc``) or a non-container element raises :class:`InvalidPathError`
    so the projection layer reports an invalid-path projection error (Req 8.16).
    """
    if is_dataclass(element) and not isinstance(element, type):
        if hasattr(element, subfield):
            return getattr(element, subfield)
        raise InvalidPathError(path, f"non-existent subfield {subfield!r}")
    if isinstance(element, dict):
        if subfield in element:
            return element[subfield]
        raise InvalidPathError(path, f"non-existent subfield {subfield!r}")
    raise InvalidPathError(
        path,
        f"cannot read subfield {subfield!r} from {type(element).__name__} element",
    )


def _resolve_index(container: Any, segment: IndexSegment, path: str) -> Any:
    """Resolve an :class:`IndexSegment` against ``container``.

    Returns :data:`MISSING` when the list field is absent/null or empty (no value to
    index). Raises :class:`InvalidPathError` for an out-of-range index into a
    non-empty list (e.g. ``phones[50]``) or when the named field is not a list.
    """
    value = _get_attr(container, segment.name, path)
    if value is MISSING or value is None:
        return MISSING
    if not isinstance(value, list):
        raise InvalidPathError(
            path, f"{segment.name!r} is not a list (cannot index)"
        )
    if len(value) == 0:
        # An empty list carries no value at any index -> treat as an absent field.
        return MISSING
    if segment.index >= len(value):
        raise InvalidPathError(
            path,
            f"index {segment.index} out of range for {segment.name!r} "
            f"(length {len(value)})",
        )
    return value[segment.index]


def _resolve_projection(container: Any, segment: ProjectionSegment, path: str) -> Any:
    """Resolve a :class:`ProjectionSegment` against ``container``.

    Returns :data:`MISSING` when the list field is absent/null. Otherwise returns a
    list: for a bare projection the elements themselves, and for a subfield
    projection the named subfield from each element. A non-existent subfield on any
    element raises :class:`InvalidPathError` (Req 8.7, 8.16).
    """
    value = _get_attr(container, segment.name, path)
    if value is MISSING or value is None:
        return MISSING
    if not isinstance(value, list):
        raise InvalidPathError(
            path, f"{segment.name!r} is not a list (cannot project)"
        )
    if segment.subfield is None:
        return list(value)
    return [
        _get_subfield_for_projection(element, segment.subfield, path)
        for element in value
    ]


def resolve_path(record: Any, path: str) -> Any:
    """Resolve a canonical ``path`` against a ``record`` (read-only).

    Walks the parsed segments through nested dataclasses, dicts, and lists. The
    resolver never mutates ``record``.

    Outcomes (Req 8.5, 8.6, 8.7, 8.16):

    * Returns the resolved value for a valid path. An array projection returns a
      ``list`` (possibly containing ``None`` subfield values or empty when the
      projected list is empty).
    * Returns :data:`MISSING` when the path targets an absent field -- a missing
      dict key, a ``None`` value anywhere along the path, or an index into an empty
      list. The caller applies its ``on_missing`` behavior.
    * Raises :class:`InvalidPathError` for an out-of-range list index, a
      non-existent subfield, or a path that otherwise cannot be navigated.

    Parameters
    ----------
    record:
        The ``CanonicalRecord`` (or any dataclass/dict/list structure) to read from.
    path:
        A canonical path string conforming to the canonical path grammar.

    Returns
    -------
    Any
        The resolved value, a projected list, or :data:`MISSING`.

    Raises
    ------
    InvalidPathError
        For a malformed path or an invalid resolution (out-of-range index or
        non-existent subfield).
    """
    segments = parse_path(path)
    current: Any = record
    for segment in segments:
        if current is MISSING:
            return MISSING
        if isinstance(segment, ProjectionSegment):
            current = _resolve_projection(current, segment, path)
        elif isinstance(segment, IndexSegment):
            current = _resolve_index(current, segment, path)
        else:  # FieldSegment
            current = _get_attr(current, segment.name, path)

    # A terminal null canonical value is treated as an absent field so the
    # projection layer can apply its on_missing behavior uniformly.
    if current is None:
        return MISSING
    return current

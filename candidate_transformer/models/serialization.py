"""Deterministic, JSON-ready serialization helpers for the data models.

Provides :func:`to_dict` (and named convenience wrappers) that convert the
dataclass-based models into plain ``dict``/``list`` structures suitable for
deterministic JSON output later in the pipeline.

Determinism: :func:`dataclasses.asdict` preserves dataclass field declaration
order and ``dict`` insertion order, so serializing the same record twice yields
structurally identical output (Req 12.1). Callers that need canonical JSON text
should still serialize with stable settings (e.g.
``json.dumps(..., sort_keys=False, ensure_ascii=False)``); ordering here is
already deterministic by construction.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from .canonical import CanonicalRecord
from .run_result import RunResult


def to_dict(obj: Any) -> Any:
    """Recursively convert a data model into a plain, JSON-ready structure.

    Dataclass instances become ``dict``s (field order preserved); lists and dicts
    are converted element-wise. Scalars pass through unchanged.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    return obj


def canonical_record_to_dict(record: CanonicalRecord) -> dict[str, Any]:
    """Serialize a :class:`CanonicalRecord` to a JSON-ready dict (deterministic)."""
    return asdict(record)


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    """Serialize a :class:`RunResult` to a JSON-ready dict (deterministic)."""
    return asdict(result)

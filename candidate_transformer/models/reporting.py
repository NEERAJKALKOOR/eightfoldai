"""Structured error and logging data structures.

Implements :class:`ErrorReport` (``{source, stage, error}``, Req 11.1) and
:class:`LogEntry` (``{timestamp, level, module, message}``, Req 11.3) from the
design's Data Models section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Pipeline stages an error can be tagged with (Req 11.2).
Stage = Literal[
    "ingest",
    "extract",
    "normalize",
    "resolve",
    "merge",
    "project",
    "validate",
]

# Severity classification of a log entry (Logging_Level).
LogLevel = Literal["INFO", "WARNING", "ERROR"]


@dataclass
class ErrorReport:
    """A structured error object of shape ``{ source, stage, error }`` (Req 11.1).

    ``source`` identifies the originating Source (``None`` when not source-specific),
    ``stage`` is the pipeline stage that failed (Req 11.2), and ``error`` is a
    human-readable description.
    """

    source: str | None
    stage: str
    error: str


@dataclass
class LogEntry:
    """A structured log record of shape ``{ timestamp, level, module, message }``.

    ``timestamp`` is operational metadata (ISO-8601) and is intentionally never
    part of the canonical output, preserving determinism of the projected profile.
    """

    timestamp: str
    level: str
    module: str
    message: str

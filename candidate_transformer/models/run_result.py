"""Top-level run result data structure.

Implements :class:`RunResult` (``{profiles, errors, exit_code}``) from the
design's Data Models section. The top-level runner always returns a
:class:`RunResult` — no exception escapes a run (Req 10.5, 18.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .reporting import ErrorReport


@dataclass
class RunResult:
    """The result of a full pipeline run.

    ``profiles`` is the list of projected profiles (one per identity group),
    ``errors`` collects every :class:`ErrorReport` produced during the run, and
    ``exit_code`` is ``0`` on a clean run and non-zero when any error occurred.
    """

    profiles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[ErrorReport] = field(default_factory=list)
    exit_code: int = 0

    @classmethod
    def empty(cls) -> "RunResult":
        """Return an empty, clean (exit_code 0) run result."""
        return cls()

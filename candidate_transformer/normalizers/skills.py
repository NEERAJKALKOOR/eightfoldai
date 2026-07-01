"""Skill normalization against a configurable, layered vocabulary (Req 3.7, 3.8, 3.10).

The vocabulary is **data, not code**: it is loaded from an external
``config/skills.json`` file so new technologies can be added without touching the
source. Matching is **layered** so the normalizer degrades gracefully instead of
dropping everything it has not seen verbatim:

    raw skill
      -> exact canonical match    (quality 1.0)
      -> alias match              (quality 0.8)
      -> fuzzy match (typos)      (quality 0.6, RapidFuzz ratio >= threshold)
      -> None                     (unknown; the caller may keep it as an
                                   ``unknown_skill`` rather than invent a value)

This keeps the system **deterministic and explainable** (no LLM, no network): the
dictionary is a fixed file, alias matching is exact, and fuzzy matching uses a
fixed RapidFuzz ratio threshold with a deterministic tie-break.

Design contract:

* A value equal to a canonical name (case/whitespace-insensitive) scores ``1.0``.
* A value matched via an alias scores ``0.8``.
* A value within the fuzzy threshold of a known surface form scores ``0.6``.
* A value matching nothing yields ``(None, 0.0)`` — the normalizer never invents a
  canonical name (Req 3.8, null-honesty). Surfacing the raw unknown string is the
  job of the merge stage (``unknown_skills``), not this function.

The function is deterministic and never raises on bad input (``None``, ``42``,
``""`` all resolve to ``(None, 0.0)``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .common import NULL_RESULT, NormalizationResult

# Quality scores per matching tier.
_QUALITY_EXACT = 1.0
_QUALITY_ALIAS = 0.8
_QUALITY_FUZZY = 0.6

# Fuzzy tier: a RapidFuzz ``ratio`` (0-100 normalized Levenshtein similarity) at or
# above this threshold is accepted. Kept high so genuine typos resolve while
# unrelated tokens stay unknown, preserving the null-honesty contract.
_FUZZY_THRESHOLD = 90.0

# The minimum comparison-key length eligible for fuzzy matching. Very short tokens
# (1-2 chars like "go", "ml") fuzzy-match far too easily, so fuzzy is reserved for
# longer tokens; short tokens still resolve via exact/alias.
_FUZZY_MIN_LEN = 4


# ---------------------------------------------------------------------------
# Built-in fallback vocabulary
# ---------------------------------------------------------------------------
# Used only if the external ``config/skills.json`` cannot be found or parsed, so
# the normalizer always has a working vocabulary even outside the repo checkout.
def _default_vocabulary() -> dict[str, list[str]]:
    """Return the built-in fallback vocabulary (the original curated set)."""
    return {
        "Python": ["py", "python3", "python programming"],
        "JavaScript": ["js", "javascript", "java script", "ecmascript"],
        "TypeScript": ["ts", "type script"],
        "Java": ["jdk", "java se"],
        "Go": ["golang", "go lang"],
        "Rust": ["rust-lang", "rustlang"],
        "Docker": ["docker engine", "dockerized"],
        "Kubernetes": ["k8s", "kube"],
        "PostgreSQL": ["postgres", "postgre sql", "psql", "postgresql"],
        "Terraform": ["tf", "hashicorp terraform"],
        "C++": ["cpp", "cplusplus", "c plus plus"],
        "C#": ["csharp", "c sharp", "dotnet", ".net"],
        "SQL": ["structured query language"],
        "React": ["react.js", "reactjs", "react js"],
        "Node.js": ["node", "nodejs", "node js"],
        "AWS": ["amazon web services"],
    }


def _candidate_config_paths() -> list[Path]:
    """Return the ordered locations to look for the external skills dictionary.

    Resolution order (first existing wins):

    1. the ``CANDIDATE_TRANSFORMER_SKILLS`` environment variable (an explicit path),
    2. ``<repo-root>/config/skills.json`` (the editable project dictionary),
    3. ``skills.json`` packaged next to this module (installed fallback).
    """
    paths: list[Path] = []
    env = os.getenv("CANDIDATE_TRANSFORMER_SKILLS")
    if env:
        paths.append(Path(env))
    here = Path(__file__).resolve()
    # candidate_transformer/normalizers/skills.py -> repo root is parents[2].
    paths.append(here.parents[2] / "config" / "skills.json")
    paths.append(here.parent / "skills.json")
    return paths


def load_vocabulary(path: str | os.PathLike[str] | None = None) -> dict[str, list[str]]:
    """Load the skill vocabulary from ``path`` (or the default search locations).

    The file is a JSON object mapping each ``Canonical_Skill_Name`` to a list of
    accepted aliases. Values are coerced to ``list[str]``. On any failure (missing
    file, unreadable, malformed JSON, wrong shape) the built-in fallback vocabulary
    is returned, so loading never raises.
    """
    search = [Path(path)] if path is not None else _candidate_config_paths()
    for candidate in search:
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    return {
                        str(name): [str(a) for a in (aliases or [])]
                        for name, aliases in data.items()
                    }
        except (OSError, ValueError):
            continue
    return _default_vocabulary()


def _canonicalize_key(raw: Any) -> str | None:
    """Return a comparison key for ``raw`` (lowercased, whitespace-collapsed).

    Returns ``None`` for any input that is not a non-empty string once trimmed,
    so callers can short-circuit to ``(None, 0.0)``. Never raises.
    """
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split())
    if not collapsed:
        return None
    return collapsed.lower()


def _build_alias_index(vocabulary: dict[str, list[str]]) -> dict[str, str]:
    """Build a reverse ``surface-form-key -> Canonical_Skill_Name`` lookup.

    Canonical names and aliases are reduced to comparison keys (lowercased,
    whitespace-collapsed) so lookup is O(1) and case/whitespace insensitive. The
    canonical name's own key is included; an alias never clobbers a canonical key.
    """
    index: dict[str, str] = {}
    for canonical, aliases in vocabulary.items():
        canonical_key = _canonicalize_key(canonical)
        if canonical_key is not None:
            index.setdefault(canonical_key, canonical)
        for alias in aliases:
            alias_key = _canonicalize_key(alias)
            if alias_key is not None:
                index.setdefault(alias_key, canonical)
    return index


# ---------------------------------------------------------------------------
# Module state: the active vocabulary and its derived indexes.
# ---------------------------------------------------------------------------
Controlled_Skill_Vocabulary: dict[str, list[str]] = load_vocabulary()
_ALIAS_INDEX: dict[str, str] = _build_alias_index(Controlled_Skill_Vocabulary)
_CANONICAL_KEYS: frozenset[str] = frozenset(
    key
    for name in Controlled_Skill_Vocabulary
    if (key := _canonicalize_key(name)) is not None
)
# Surface-form keys sorted once for a deterministic fuzzy-match scan order.
_SORTED_SURFACE_KEYS: tuple[str, ...] = tuple(sorted(_ALIAS_INDEX))


def set_vocabulary(vocabulary: dict[str, list[str]]) -> None:
    """Replace the active vocabulary and rebuild the derived indexes.

    Lets callers/tests swap in a custom dictionary at runtime (Option 1: the
    vocabulary is configuration, not code). Rebuilds are deterministic.
    """
    global Controlled_Skill_Vocabulary, _ALIAS_INDEX, _CANONICAL_KEYS
    global _SORTED_SURFACE_KEYS
    Controlled_Skill_Vocabulary = dict(vocabulary)
    _ALIAS_INDEX = _build_alias_index(Controlled_Skill_Vocabulary)
    _CANONICAL_KEYS = frozenset(
        key
        for name in Controlled_Skill_Vocabulary
        if (key := _canonicalize_key(name)) is not None
    )
    _SORTED_SURFACE_KEYS = tuple(sorted(_ALIAS_INDEX))


def _fuzzy_match(key: str) -> str | None:
    """Return the canonical name whose surface form is the closest fuzzy match.

    Compares ``key`` against every surface form (canonical names + aliases) using
    the RapidFuzz ``ratio`` and returns the best canonical name when the score is at
    or above :data:`_FUZZY_THRESHOLD`. Scanning the pre-sorted keys makes ties
    resolve deterministically (the lexicographically smaller surface key wins).
    Short keys are excluded to avoid spurious matches.
    """
    if len(key) < _FUZZY_MIN_LEN:
        return None
    best_canonical: str | None = None
    best_score = 0.0
    for surface_key in _SORTED_SURFACE_KEYS:
        score = fuzz.ratio(key, surface_key)
        if score > best_score:
            best_score = score
            best_canonical = _ALIAS_INDEX[surface_key]
    if best_canonical is not None and best_score >= _FUZZY_THRESHOLD:
        return best_canonical
    return None


def normalize_skill(raw: Any) -> NormalizationResult:
    """Normalize a raw skill string to a ``Canonical_Skill_Name`` (Req 3.7, 3.8, 3.10).

    Applies the layered matcher (exact -> alias -> fuzzy) against the active
    vocabulary.

    Returns:
        ``(canonical, 1.0)`` for an exact canonical match, ``(canonical, 0.8)`` for
        an alias match, ``(canonical, 0.6)`` for a fuzzy (typo) match, or
        ``(None, 0.0)`` when nothing matches — never an invented value (Req 3.8).

    Deterministic; never raises on bad input.
    """
    key = _canonicalize_key(raw)
    if key is None:
        return NULL_RESULT

    canonical = _ALIAS_INDEX.get(key)
    if canonical is not None:
        quality = _QUALITY_EXACT if key in _CANONICAL_KEYS else _QUALITY_ALIAS
        return (canonical, quality)

    fuzzy = _fuzzy_match(key)
    if fuzzy is not None:
        return (fuzzy, _QUALITY_FUZZY)

    # Not in the vocabulary -> honest null (Req 3.8). The merge stage keeps the raw
    # string as an unknown_skill instead of discarding it.
    return NULL_RESULT


__all__ = [
    "Controlled_Skill_Vocabulary",
    "normalize_skill",
    "load_vocabulary",
    "set_vocabulary",
]

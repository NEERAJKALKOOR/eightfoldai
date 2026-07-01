"""Shared regex helpers for the unstructured text adapters.

Both :mod:`candidate_transformer.adapters.resume` and
:mod:`candidate_transformer.adapters.recruiter_notes` extract contact details
(emails, phones, profile links) from free-form text. The regexes and the small
"scan" helpers live here so the two adapters stay faithful and consistent and so
neither has to duplicate the patterns.

Design contract (Req 2.4, 2.6): every helper returns *only what it actually
finds*, in document order, deduplicated while preserving first-seen order. A
helper never invents a value; when nothing matches it returns an empty list (or
``None`` for the single-valued helpers). None of these helpers raise on bad
input.
"""

from __future__ import annotations

import re

__all__ = [
    "find_emails",
    "find_phones",
    "find_linkedin",
    "find_github",
    "find_skill_mentions",
]

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# A pragmatic email pattern: local@domain.tld. Kept deliberately permissive on the
# local part; normalization (lowercase/trim/validate) happens in a later stage.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A candidate phone substring: an optional leading "+", then digits interspersed
# with the usual separators (spaces, dots, dashes, parentheses). We *capture
# broadly* then *filter by digit count* (10-15 digits) so we do not mistake dates
# like "2024-03-12" (8 digits) or counts like "~6 weeks" for phone numbers.
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
_MIN_PHONE_DIGITS = 10
_MAX_PHONE_DIGITS = 15

# Profile links. The leading scheme / "www." is optional so bare
# "linkedin.com/in/janedoe" forms (common in resumes) are still captured.
_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_\-%./]+",
    re.IGNORECASE,
)
_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-%./]+",
    re.IGNORECASE,
)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Return ``items`` with later duplicates removed, first-seen order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _digit_count(text: str) -> int:
    return sum(1 for ch in text if ch.isdigit())


def find_emails(text: str) -> list[str]:
    """Return all email-like substrings in ``text`` (raw, order-preserving)."""
    if not isinstance(text, str):
        return []
    return _dedupe_preserve_order(_EMAIL_RE.findall(text))


def find_phones(text: str) -> list[str]:
    """Return phone-like substrings in ``text`` with 10-15 digits (raw, trimmed).

    Values are returned verbatim (minus surrounding whitespace) so the later
    normalization stage can convert them to E.164; only the digit-count gate is
    applied here to avoid false positives such as dates.
    """
    if not isinstance(text, str):
        return []
    matches: list[str] = []
    for raw in _PHONE_RE.findall(text):
        candidate = raw.strip()
        if _MIN_PHONE_DIGITS <= _digit_count(candidate) <= _MAX_PHONE_DIGITS:
            matches.append(candidate)
    return _dedupe_preserve_order(matches)


def find_linkedin(text: str) -> str | None:
    """Return the first LinkedIn profile URL in ``text``, or ``None``."""
    if not isinstance(text, str):
        return None
    match = _LINKEDIN_RE.search(text)
    return match.group(0).rstrip(".,;") if match else None


def find_github(text: str) -> str | None:
    """Return the first GitHub profile URL in ``text``, or ``None``."""
    if not isinstance(text, str):
        return None
    match = _GITHUB_RE.search(text)
    return match.group(0).rstrip(".,;") if match else None


def find_skill_mentions(text: str, vocabulary: dict[str, list[str]]) -> list[str]:
    """Return raw skill mentions in ``text`` matching the ``vocabulary``.

    Scans ``text`` for whole-word occurrences of every canonical name and every
    alias in ``vocabulary`` (case-insensitive). The *raw matched text* is returned
    (not the canonical name) so the value stays faithful to the source; the later
    normalization stage maps it to a ``Canonical_Skill_Name``. Results are
    de-duplicated case-insensitively, preserving first-seen order, so each distinct
    mention appears once. Never raises.
    """
    if not isinstance(text, str) or not text:
        return []

    # Build the set of surface forms (canonical names + aliases) to look for.
    surface_forms: list[str] = []
    for canonical, aliases in vocabulary.items():
        surface_forms.append(canonical)
        surface_forms.extend(aliases)

    found: list[tuple[int, str]] = []
    seen_lower: set[str] = set()
    for form in surface_forms:
        if not form:
            continue
        # Word-boundary-ish match. Skills can contain regex specials (C++, C#,
        # Node.js), so escape the form and frame it with lookarounds that treat
        # common skill punctuation as part of the token rather than a boundary.
        pattern = re.compile(
            r"(?<![A-Za-z0-9+#.])" + re.escape(form) + r"(?![A-Za-z0-9+#])",
            re.IGNORECASE,
        )
        for m in pattern.finditer(text):
            raw = m.group(0)
            key = raw.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                found.append((m.start(), raw))

    # Order by first appearance in the document for determinism.
    found.sort(key=lambda pair: pair[0])
    return [raw for _, raw in found]

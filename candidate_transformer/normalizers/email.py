"""Email normalization (Req 3.9, 3.10).

``normalize_email`` lowercases the email and removes surrounding whitespace, the
only transformations Req 3.9 mandates. Because the requirement is *best-effort*,
a non-empty value that fails the syntax check is still returned in its cleaned
form (it is never nulled) — only empty/whitespace-only or non-string/``None``
input yields the canonical null result ``(None, 0.0)``.

Quality scoring (Req 3.10):
  * ``1.0`` — the cleaned value has valid email syntax (a reasonable check:
    exactly one ``@``, a non-empty local part, a domain containing a dot with
    non-empty labels around every dot, and no whitespace).
  * ``0.7`` — the cleaned value is non-empty but fails the syntax check.

The function is deterministic, idempotent (``normalize_email(value)`` reproduces
the same ``value`` and quality once it is already normalized), and never raises.
"""

from __future__ import annotations

from .common import NULL_RESULT, NormalizationResult

# Normalization_Quality awarded to a syntactically valid email.
_QUALITY_VALID = 1.0
# Normalization_Quality awarded to a cleaned but syntactically invalid email.
_QUALITY_INVALID = 0.7


def _has_valid_syntax(email: str) -> bool:
    """Return ``True`` for a reasonable, deterministic email-syntax check.

    The check is intentionally lightweight (no external services): exactly one
    ``@``, a non-empty local part, a domain that contains a dot with non-empty
    labels surrounding every dot, and no whitespace anywhere in the value.
    """
    if any(ch.isspace() for ch in email):
        return False
    if email.count("@") != 1:
        return False

    local, _, domain = email.partition("@")
    if not local or not domain:
        return False

    # The domain must contain a dot, and every dot-separated label must be
    # non-empty (rejects dotless domains, leading/trailing dots, and "..").
    if "." not in domain:
        return False
    if any(not label for label in domain.split(".")):
        return False

    return True


def normalize_email(raw: object) -> NormalizationResult:
    """Normalize ``raw`` to a trimmed, lowercased email, returning ``(value | None, quality)``.

    Deterministic and total: never raises. Returns ``(None, 0.0)`` when ``raw`` is
    not a non-empty string after trimming. Otherwise returns the cleaned value
    with quality ``1.0`` for valid syntax or ``0.7`` for a cleaned-but-invalid
    value (the value is returned, not nulled, per Req 3.9).
    """
    if not isinstance(raw, str):
        return NULL_RESULT

    cleaned = raw.strip().lower()
    if not cleaned:
        return NULL_RESULT

    if _has_valid_syntax(cleaned):
        return (cleaned, _QUALITY_VALID)

    return (cleaned, _QUALITY_INVALID)

"""Phone number normalization to E.164 (Req 3.1, 3.2).

``normalize_phone`` converts a raw phone string into canonical E.164 form
(e.g. ``+14155552671``) using the ``phonenumbers`` library.

Quality scoring (Req 3.10):
  * ``1.0`` — the number parsed with a *confident, explicit* region. This is the
    case when the raw input carries its own ``+`` country code, so no region had
    to be assumed.
  * ``0.7`` — the number parsed only because a ``default_region`` was assumed
    (the raw input had no explicit ``+`` country code). The conversion succeeded
    but relied on a fallback assumption, so it scores lower.

Null rule (Req 3.2): any value that cannot be parsed into a valid phone number —
including ``None``, empty/garbage strings, and numbers that parse syntactically
but are not valid for their region — yields ``(None, 0.0)``. The function never
raises.
"""

from __future__ import annotations

import phonenumbers

from .common import NULL_RESULT, NormalizationResult

# Quality awarded when the country code was explicit in the input (leading ``+``).
_QUALITY_EXPLICIT_REGION = 1.0
# Quality awarded when a default region had to be assumed to parse the number.
_QUALITY_ASSUMED_REGION = 0.7


def normalize_phone(
    raw: object,
    default_region: str | None = None,
) -> NormalizationResult:
    """Normalize ``raw`` to E.164, returning ``(e164 | None, quality)``.

    Deterministic and total: never raises. Returns ``(None, 0.0)`` for any input
    that does not parse into a valid phone number (Req 3.2).
    """
    if not isinstance(raw, str):
        return NULL_RESULT

    text = raw.strip()
    if not text:
        return NULL_RESULT

    # An explicit international prefix ("+<country code>") means the region is
    # carried by the number itself; nothing has to be assumed.
    has_explicit_country_code = text.startswith("+")

    try:
        parsed = phonenumbers.parse(
            text,
            None if has_explicit_country_code else default_region,
        )
    except phonenumbers.NumberParseException:
        return NULL_RESULT

    # Reject numbers that parse structurally but are not valid (e.g. wrong length
    # for the region) — a wrong-but-confident value is worse than an empty one.
    if not phonenumbers.is_valid_number(parsed):
        return NULL_RESULT

    e164 = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )

    quality = (
        _QUALITY_EXPLICIT_REGION
        if has_explicit_country_code
        else _QUALITY_ASSUMED_REGION
    )
    return (e164, quality)

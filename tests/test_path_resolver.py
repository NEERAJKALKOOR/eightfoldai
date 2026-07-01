"""Unit tests for the canonical path grammar parser and resolver (task 11.1).

Covers the three supported path kinds -- nested (``location.city``), indexed
(``phones[0]``), and array projection (``skills[].name``) -- and the three
resolution outcomes the projection layer depends on: an absent field returns the
``MISSING`` sentinel, an out-of-range index raises ``InvalidPathError``, and a
non-existent projection subfield raises ``InvalidPathError`` (Req 8.5, 8.6, 8.7,
8.16).
"""

from __future__ import annotations

import pytest

from candidate_transformer.engine.path_resolver import (
    MISSING,
    FieldSegment,
    IndexSegment,
    InvalidPathError,
    ProjectionSegment,
    parse_path,
    resolve_path,
)
from candidate_transformer.models import (
    CanonicalRecord,
    Links,
    Location,
    Skill,
)


def _record() -> CanonicalRecord:
    """Build a populated canonical record for resolution tests."""
    return CanonicalRecord(
        candidate_id="cid-123",
        full_name="Jane Doe",
        emails=["jane@example.com"],
        phones=["+14155552671", "+442071838750"],
        location=Location(city="Seattle", region="WA", country="US"),
        links=Links(linkedin="https://linkedin.com/in/jane"),
        skills=[Skill(name="Python"), Skill(name="JavaScript")],
    )


# --- Parser ----------------------------------------------------------------


def test_parse_nested_path():
    assert parse_path("location.city") == [
        FieldSegment("location"),
        FieldSegment("city"),
    ]


def test_parse_indexed_path():
    assert parse_path("phones[0]") == [IndexSegment("phones", 0)]


def test_parse_array_projection_with_subfield():
    assert parse_path("skills[].name") == [ProjectionSegment("skills", "name")]


def test_parse_bare_array_projection():
    assert parse_path("skills[]") == [ProjectionSegment("skills", None)]


def test_parse_rejects_projection_that_is_not_terminal():
    with pytest.raises(InvalidPathError):
        parse_path("skills[].name.extra")


def test_parse_rejects_empty_path():
    with pytest.raises(InvalidPathError):
        parse_path("")


# --- Nested resolution -----------------------------------------------------


def test_resolve_nested_path():
    record = _record()
    assert resolve_path(record, "location.city") == "Seattle"
    assert resolve_path(record, "location.country") == "US"


def test_resolve_top_level_field():
    assert resolve_path(_record(), "full_name") == "Jane Doe"


# --- Indexed resolution ----------------------------------------------------


def test_resolve_indexed_element():
    record = _record()
    assert resolve_path(record, "phones[0]") == "+14155552671"
    assert resolve_path(record, "phones[1]") == "+442071838750"


def test_resolve_out_of_range_index_raises():
    record = _record()
    with pytest.raises(InvalidPathError) as exc:
        resolve_path(record, "phones[50]")
    assert exc.value.path == "phones[50]"


# --- Array projection ------------------------------------------------------


def test_resolve_array_projection_subfield():
    record = _record()
    assert resolve_path(record, "skills[].name") == ["Python", "JavaScript"]


def test_resolve_bare_array_projection_returns_elements():
    record = _record()
    result = resolve_path(record, "skills[]")
    assert result == [Skill(name="Python"), Skill(name="JavaScript")]


def test_resolve_nonexistent_subfield_raises():
    record = _record()
    with pytest.raises(InvalidPathError) as exc:
        resolve_path(record, "skills[].abc")
    assert exc.value.path == "skills[].abc"


# --- Absent field (MISSING sentinel) ---------------------------------------


def test_resolve_absent_scalar_field_returns_missing():
    # full_name is None on an all-null record -> absent.
    record = CanonicalRecord.empty()
    assert resolve_path(record, "full_name") is MISSING


def test_resolve_absent_nested_field_returns_missing():
    record = _record()  # city set, region set, but no portfolio link
    assert resolve_path(record, "links.portfolio") is MISSING


def test_resolve_index_into_empty_list_returns_missing():
    record = CanonicalRecord.empty()  # emails == []
    assert resolve_path(record, "emails[0]") is MISSING


def test_resolve_projection_over_absent_list_returns_missing():
    # links.other dict-style absence; here use a dict record for key absence.
    record = {"emails": ["a@b.com"]}
    assert resolve_path(record, "phones[0]") is MISSING


# --- Read-only guarantee ---------------------------------------------------


def test_resolver_does_not_mutate_record():
    record = _record()
    before = (
        record.full_name,
        list(record.phones),
        record.location.city,
        [s.name for s in record.skills],
    )
    resolve_path(record, "skills[].name")
    resolve_path(record, "phones[0]")
    resolve_path(record, "location.city")
    after = (
        record.full_name,
        list(record.phones),
        record.location.city,
        [s.name for s in record.skills],
    )
    assert before == after

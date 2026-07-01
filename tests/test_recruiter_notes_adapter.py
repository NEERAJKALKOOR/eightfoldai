"""Example unit tests for the Recruiter notes adapter (Task 5.6).

A known plain-text recruiter-notes fixture yields a single ``PerSourceRecord``
with the correct ``source_type``/``source_id`` (Req 1.9) and the documented
lightweight regex extraction of emails, phones, the candidate name, and mentioned
skills (Req 1.8). Each value records its extraction method for provenance
(Req 2.5); absent signals are recorded null, never invented (Req 2.4, 2.6).

_Requirements: 1.8, 1.9, 2.5_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import (
    RecruiterNotesAdapter,
    build_default_registry,
)
from candidate_transformer.adapters.base import (
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import PerSourceRecord

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "notes.txt"


@pytest.fixture()
def adapter() -> RecruiterNotesAdapter:
    return RecruiterNotesAdapter()


def _extract(adapter: RecruiterNotesAdapter) -> PerSourceRecord:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE)))
    records = adapter.extract(raw)
    assert len(records) == 1
    return records[0]


# -- attributes / provenance anchors --------------------------------------


def test_adapter_attributes_come_from_shared_tables(
    adapter: RecruiterNotesAdapter,
) -> None:
    assert adapter.source_type == "recruiter_notes"
    assert adapter.reliability == reliability_of("recruiter_notes") == 0.60
    assert adapter.priority == priority_of("recruiter_notes") == 5


def test_source_provenance_recorded(adapter: RecruiterNotesAdapter) -> None:
    rec = _extract(adapter)
    assert rec.source_type == "recruiter_notes"
    assert rec.source_id == str(FIXTURE)


# -- documented regex extraction (Req 1.8) --------------------------------


def test_name_email_phone_skills_extracted(adapter: RecruiterNotesAdapter) -> None:
    rec = _extract(adapter)
    # The "Call notes" header is skipped; the first plausible person name wins.
    assert rec.values["full_name"].value == "Jane Doe"
    assert rec.values["emails"].value == "jane.doe@example.com"
    # Phone captured verbatim; normalization is a later stage.
    assert rec.values["phones"].value == "415.555.2671"
    # Skills are raw mentions matched against the controlled vocabulary.
    assert rec.values["skills"].value is not None
    skill_lower = {s.lower() for s in rec.values["skills"].value}
    assert {"py", "js", "rust"} <= skill_lower


def test_extraction_methods_recorded(adapter: RecruiterNotesAdapter) -> None:
    rec = _extract(adapter)
    assert rec.values["full_name"].method == "regex_name"
    assert rec.values["emails"].method == "regex_email"
    assert rec.values["phones"].method == "regex_phone"
    assert rec.values["skills"].method == "regex_skill"


# -- null-honesty on empty input (Req 2.4, 2.6, 10.2) ---------------------


def test_empty_notes_yield_all_null_values(adapter: RecruiterNotesAdapter) -> None:
    raw = RawSource(source_id="empty.txt", source_type="recruiter_notes", content="")
    rec = adapter.extract(raw)[0]
    assert rec.values["full_name"].value is None
    assert rec.values["emails"].value is None
    assert rec.values["phones"].value is None
    assert rec.values["skills"].value is None


# -- registry routing ------------------------------------------------------


def test_default_registry_resolves_txt_reference() -> None:
    registry = build_default_registry()
    resolved = registry.resolve(SourceRef(location="notes.txt"))
    assert isinstance(resolved, RecruiterNotesAdapter)

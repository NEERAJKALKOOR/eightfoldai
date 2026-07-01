"""Unit tests for the SourceAdapter contract, priority tables, and registry.

Covers task 5.1: the interface/types, the SourcePriority / Source_Reliability
tables, and deterministic resolution by the AdapterRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from candidate_transformer.adapters import (
    SOURCE_PRIORITY,
    SOURCE_RELIABILITY,
    AdapterRegistry,
    IngestError,
    NoAdapterFoundError,
    RawSource,
    SourceAdapter,
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import FieldValue, PerSourceRecord


@dataclass
class DummyAdapter:
    """A minimal adapter implementing the SourceAdapter protocol.

    Recognizes refs whose location ends with ``suffix`` (or whose explicit
    ``source_type`` hint matches). Records which calls were made for assertions.
    """

    source_type: str
    reliability: float
    priority: int
    suffix: str
    calls: list[str] = field(default_factory=list)

    def can_handle(self, ref: SourceRef) -> bool:
        if ref.source_type is not None:
            return ref.source_type == self.source_type
        return ref.location.endswith(self.suffix)

    def ingest(self, ref: SourceRef) -> RawSource:
        self.calls.append("ingest")
        return RawSource(
            source_id=ref.location,
            source_type=self.source_type,  # type: ignore[arg-type]
            content="raw",
            ref=ref,
        )

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        self.calls.append("extract")
        return [
            PerSourceRecord(
                source_id=raw.source_id,
                source_type=raw.source_type,
                values={"full_name": FieldValue(value="X", method="dummy", normalization_quality=1.0)},
            )
        ]


def make_csv_adapter() -> DummyAdapter:
    return DummyAdapter(
        source_type="recruiter_csv",
        reliability=reliability_of("recruiter_csv"),
        priority=priority_of("recruiter_csv"),
        suffix=".csv",
    )


def make_notes_adapter() -> DummyAdapter:
    return DummyAdapter(
        source_type="recruiter_notes",
        reliability=reliability_of("recruiter_notes"),
        priority=priority_of("recruiter_notes"),
        suffix=".txt",
    )


def test_priority_table_order_and_ranks():
    assert SOURCE_PRIORITY == (
        "recruiter_csv",
        "ats_json",
        "resume",
        "linkedin",
        "github",
        "recruiter_notes",
    )
    # lower rank == more authoritative
    assert priority_of("recruiter_csv") == 0
    assert priority_of("recruiter_notes") == 5
    assert priority_of("recruiter_csv") < priority_of("github")
    # unknown sorts after all known types
    assert priority_of("mystery") == len(SOURCE_PRIORITY)


def test_reliability_table_weights():
    assert SOURCE_RELIABILITY == {
        "recruiter_csv": 0.95,
        "ats_json": 0.90,
        "resume": 0.85,
        "linkedin": 0.80,
        "github": 0.70,
        "recruiter_notes": 0.60,
    }
    assert reliability_of("resume") == 0.85
    assert reliability_of("mystery") == 0.0


def test_dummy_adapter_satisfies_protocol():
    adapter = make_csv_adapter()
    # runtime_checkable protocol membership
    assert isinstance(adapter, SourceAdapter)


def test_register_and_resolve_picks_matching_adapter():
    registry = AdapterRegistry()
    csv = make_csv_adapter()
    notes = make_notes_adapter()
    registry.register(csv)
    registry.register(notes)

    resolved = registry.resolve(SourceRef(location="/data/recruiter.csv"))
    assert resolved is csv

    resolved_notes = registry.resolve(SourceRef(location="/data/notes.txt"))
    assert resolved_notes is notes


def test_resolve_round_trips_through_ingest_and_extract():
    registry = AdapterRegistry([make_csv_adapter()])
    ref = SourceRef(location="/data/recruiter.csv")
    adapter = registry.resolve(ref)
    raw = adapter.ingest(ref)
    records = adapter.extract(raw)
    assert raw.source_id == "/data/recruiter.csv"
    assert raw.source_type == "recruiter_csv"
    assert records[0].source_type == "recruiter_csv"
    assert records[0].values["full_name"].method == "dummy"


def test_resolution_is_order_independent_and_priority_ranked():
    """Two adapters both claim the ref; the more authoritative one wins regardless
    of registration order."""
    high = DummyAdapter("recruiter_csv", 0.95, 0, suffix=".dat")
    low = DummyAdapter("recruiter_notes", 0.60, 5, suffix=".dat")

    reg_a = AdapterRegistry([high, low])
    reg_b = AdapterRegistry([low, high])

    ref = SourceRef(location="/data/thing.dat")
    assert reg_a.resolve(ref).source_type == "recruiter_csv"
    assert reg_b.resolve(ref).source_type == "recruiter_csv"


def test_explicit_source_type_hint_forces_adapter():
    csv = make_csv_adapter()
    notes = make_notes_adapter()
    registry = AdapterRegistry([csv, notes])
    # Hint overrides suffix-based sniffing.
    ref = SourceRef(location="/ambiguous", source_type="recruiter_notes")
    assert registry.resolve(ref) is notes


def test_resolve_raises_when_no_adapter_handles():
    registry = AdapterRegistry([make_csv_adapter()])
    with pytest.raises(NoAdapterFoundError):
        registry.resolve(SourceRef(location="/data/profile.json"))


def test_adapters_property_returns_priority_order_copy():
    notes = make_notes_adapter()
    csv = make_csv_adapter()
    registry = AdapterRegistry([notes, csv])  # registered low-priority first
    ordered = registry.adapters
    assert [a.source_type for a in ordered] == ["recruiter_csv", "recruiter_notes"]
    # property returns a copy; mutating it does not affect the registry
    ordered.clear()
    assert len(registry.adapters) == 2


def test_ingest_error_is_an_exception():
    assert issubclass(IngestError, Exception)

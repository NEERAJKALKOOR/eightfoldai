"""Scale / memory integration test for the TransformerEngine (Task 14.6).

Exercises a single large batch run over *thousands* of synthetic candidates to
assert the two scale guarantees the design promises:

* **Completion at scale** -- a run with source data for thousands of candidates
  processes every candidate and produces one ``Projected_Profile`` per identity
  group, with a clean exit and no errors (Req 14.1, 14.2).
* **Bounded memory** -- per-candidate isolation keeps peak memory modest relative
  to the batch size; the run does not exhaust available memory (Req 14.2, 19.1).

The batch is a single Recruiter CSV with ``N`` rows, one *distinct* candidate per
row: a globally unique email and phone, and a full name that is the row index
rendered as a fixed-width, zero-padded 6-digit string. Distinct emails and phones
defeat the exact-match identity rules (rules 1-4). The fixed-width numeric names
defeat the RapidFuzz name-similarity rule (rule 5): any two distinct 6-digit strings
have the same length and differ in at least one position, so their longest matching
run is at most 5 of 12 characters and the RapidFuzz ratio is at most ~0.83 -- safely
below the ``> 0.9`` merge threshold, with no shared prefix/suffix to inflate it.
(Natural-language names were tried first but merged heavily, because two names that
share long words exceed 0.9.) Every row therefore forms its own identity group:
``N`` rows -> ``N`` per-source records -> ``N`` identity groups -> ``N`` profiles.

Peak memory is measured with :mod:`tracemalloc`. The assertion uses a deliberately
generous bound so the test is deterministic and not flaky across machines.

_Requirements: 14.2, 19.1_
"""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine import ProjectionConfig, TransformerEngine
from candidate_transformer.models import RunResult

# Number of synthetic candidates. "Thousands" but small enough to keep the (O(n^2)
# pairwise identity resolution) run quick in CI. Must stay < 1_000_000 so the
# 6-digit zero-padded name stays fixed-width.
N_CANDIDATES = 2000

# A generous peak-memory bound (in MiB), chosen well above the observed peak so the
# assertion is robust across machines and Python builds rather than tight/flaky.
PEAK_MEMORY_BOUND_MIB = 300.0


def _unique_name(index: int) -> str:
    """A fixed-width, zero-padded numeric name; distinct names never merge (rule 5)."""
    return f"{index:06d}"


def _write_batch_csv(path: Path, n: int) -> None:
    """Write a Recruiter CSV describing ``n`` distinct synthetic candidates."""
    lines = ["name,email,phone,current_company,title"]
    for i in range(n):
        name = _unique_name(i)
        email = f"candidate{i}@example.com"
        phone = f"+1-415-{200 + i // 10000:03d}-{i % 10000:04d}"
        company = f"Company {i % 250}"
        title = f"Engineer Level {i % 12}"
        lines.append(f"{name},{email},{phone},{company},{title}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _simple_config() -> ProjectionConfig:
    """A minimal projection: id + name + primary email, no provenance bloat."""
    return ProjectionConfig.from_dict(
        {
            "include_provenance": False,
            "include_confidence": True,
            "fields": [
                {
                    "name": "id",
                    "from": "candidate_id",
                    "type": "string",
                    "required": True,
                    "on_missing": "error",
                },
                {"name": "name", "from": "full_name", "type": "string", "on_missing": "null"},
                {"name": "email", "from": "emails[0]", "type": "string", "on_missing": "null"},
            ],
        }
    )


def test_large_batch_completes_with_bounded_memory(tmp_path: Path) -> None:
    """A thousands-candidate batch completes cleanly within a bounded memory peak.

    _Requirements: 14.2, 19.1_
    """
    csv_path = tmp_path / "large_batch.csv"
    _write_batch_csv(csv_path, N_CANDIDATES)

    engine = TransformerEngine()
    ref = SourceRef(location=str(csv_path), source_type="recruiter_csv")
    config = _simple_config()

    # Measure peak memory and wall time across the full run.
    tracemalloc.start()
    start = time.perf_counter()
    result = engine.run([ref], config)
    elapsed = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mib = peak / (1024 * 1024)

    # Visible in `pytest -s` for observed peak/timing reporting.
    print(
        f"\n[scale] N={N_CANDIDATES} profiles={len(result.profiles)} "
        f"errors={len(result.errors)} peak={peak_mib:.1f} MiB "
        f"time={elapsed:.2f}s"
    )

    # Completion: a RunResult with one profile per candidate, clean exit, no errors.
    assert isinstance(result, RunResult)
    assert len(result.profiles) == N_CANDIDATES
    assert result.exit_code == 0
    assert result.errors == []

    # Every row formed its own identity group -> all candidate ids are distinct.
    ids = [profile["id"] for profile in result.profiles]
    assert len(set(ids)) == N_CANDIDATES

    # Each projected profile carries exactly the configured shape.
    sample = result.profiles[0]
    assert set(sample) == {"id", "name", "email", "overall_confidence"}

    # Bounded memory: peak stays under a generous threshold (Req 14.2, 19.1).
    assert peak_mib < PEAK_MEMORY_BOUND_MIB, (
        f"peak memory {peak_mib:.1f} MiB exceeded bound {PEAK_MEMORY_BOUND_MIB} MiB"
    )

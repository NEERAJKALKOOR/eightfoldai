"""Property-based test for per-candidate batch isolation.

Feature: candidate-data-transformer, Property 17

Property 17: Per-candidate batch isolation
    *For any* batch of candidates, the ``Projected_Profile`` produced for a given
    candidate is identical to the profile produced when that candidate's sources are
    processed alone, and a failure injected into one candidate does not change the
    output of any other candidate.

**Validates: Requirements 14.1, 14.3, 14.4**

Strategy
--------
Each candidate is described by a unique token that seeds a distinct email, phone,
and full name, so the candidates form separate identity groups (distinct emails and
phones fail identity rules 1-4; distinct random names keep the RapidFuzz name
similarity at or below the 0.9 rule-5 threshold). Each candidate is written to its
own one-row Recruiter CSV file in a per-example temp directory, and a batch is the
list of all those CSV references.

The test asserts two halves of the property:

1. **Solo == batch.** Each candidate processed alone yields a profile identical to
   that same candidate's profile within the full-batch run (matched by candidate id),
   so batching does not change any candidate's output (Req 14.1, 14.3).
2. **Failure isolation.** Injecting a failing source (a reference to a missing file)
   into the batch leaves every other candidate's profile byte-identical to the
   failure-free batch and still completes the run with a structured Error_Report
   (Req 14.4).
"""

from __future__ import annotations

import os
import string
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine.projection import ProjectionConfig
from candidate_transformer.engine.transformer import TransformerEngine

# A projection config that surfaces the candidate id (for matching) plus a few
# canonical fields, with provenance and confidence included so the comparison
# covers the full projected output, not just scalar values.
_CONFIG = ProjectionConfig.from_dict(
    {
        "include_provenance": True,
        "include_confidence": True,
        "fields": [
            {"name": "id", "from": "candidate_id", "type": "string",
             "required": True, "on_missing": "error"},
            {"name": "name", "from": "full_name", "on_missing": "null"},
            {"name": "primary_email", "from": "emails[0]", "on_missing": "null"},
            {"name": "emails", "from": "emails", "on_missing": "null"},
            {"name": "phones", "from": "phones", "on_missing": "null"},
            {"name": "headline", "from": "headline", "on_missing": "null"},
        ],
    }
)

# Unique, low-similarity tokens: random lowercase-alphanumeric strings of length >= 8.
# ``unique=True`` guarantees distinct identities; the length floor keeps any pairwise
# name similarity at or below the rule-5 (>0.9) threshold so candidates never merge.
_token = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=8, max_size=12)
_candidates = st.lists(_token, min_size=2, max_size=4, unique=True)


def _write_candidate_csv(directory: str, index: int, token: str) -> str:
    """Write a one-row Recruiter CSV for a single candidate; return its path."""
    email = f"{token}@example.com"
    # A distinct, parseable-looking US phone per candidate (validity is irrelevant to
    # the property: solo and batch normalize it identically).
    phone = f"+1415555{1000 + index:04d}"
    # The full name is the unique token alone -> distinct, low-similarity names.
    name = token
    path = os.path.join(directory, f"candidate_{index}_{token}.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("name,email,phone,current_company,title\n")
        fh.write(f"{name},{email},{phone},Acme Corp,Engineer\n")
    return path


def _profiles_by_id(profiles: list[dict]) -> dict[str, dict]:
    """Index a run's projected profiles by their projected ``id`` field."""
    return {profile["id"]: profile for profile in profiles}


@settings(deadline=None)
@given(tokens=_candidates)
def test_per_candidate_batch_isolation(tokens: list[str]) -> None:
    engine = TransformerEngine()

    with tempfile.TemporaryDirectory() as directory:
        csv_paths = [
            _write_candidate_csv(directory, i, token)
            for i, token in enumerate(tokens)
        ]

        # --- Solo: process each candidate's sources alone. -------------------
        solo_profiles: dict[str, dict] = {}
        for path in csv_paths:
            result = engine.run([SourceRef(location=path)], _CONFIG)
            assert result.exit_code == 0
            assert len(result.profiles) == 1
            solo = result.profiles[0]
            solo_profiles[solo["id"]] = solo

        # Distinct identities -> one group per candidate, no accidental merging.
        assert len(solo_profiles) == len(csv_paths)

        # --- Batch: process all candidates together. -------------------------
        batch_refs = [SourceRef(location=path) for path in csv_paths]
        batch_result = engine.run(batch_refs, _CONFIG)
        assert batch_result.exit_code == 0
        batch_profiles = _profiles_by_id(batch_result.profiles)

        # Property 17a: each candidate's batch profile is identical to its solo
        # profile -- batching changes nothing (Req 14.1, 14.3).
        assert set(batch_profiles) == set(solo_profiles)
        for candidate_id, solo in solo_profiles.items():
            assert batch_profiles[candidate_id] == solo

        # --- Failure injection: add a reference to a missing source. ---------
        missing_ref = SourceRef(location=os.path.join(directory, "missing_source.csv"))
        failing_refs = [*batch_refs, missing_ref]
        failing_result = engine.run(failing_refs, _CONFIG)

        # The run still completes and reports a structured Error_Report (Req 14.4).
        assert len(failing_result.errors) >= 1
        report = failing_result.errors[0]
        assert report.stage == "ingest"

        # Property 17b: the failure in one candidate (the missing source) leaves
        # every other candidate's output unchanged versus the failure-free batch.
        failing_profiles = _profiles_by_id(failing_result.profiles)
        assert set(failing_profiles) == set(batch_profiles)
        for candidate_id, batch_profile in batch_profiles.items():
            assert failing_profiles[candidate_id] == batch_profile

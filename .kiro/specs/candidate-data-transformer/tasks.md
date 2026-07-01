# Implementation Plan: Multi-Source Candidate Data Transformer

## Overview

This plan implements the deterministic, explainable candidate data transformer in **Python 3.11+**,
following the design's 8-stage pipeline (Ingest → Extract → Normalize → Resolve Identity → Merge →
Confidence Score → Project → Validate). It builds bottom-up: scaffold and data models first, then
pure transformation units (normalizers, adapters, identity, merge, confidence), then the projection
and validation layers, then the orchestrating engine, and finally the thin CLI, fixtures, and README.
Each step wires into the previous ones so there is no orphaned code.

Testing uses **Hypothesis** for the 17 correctness properties (minimum 100 iterations each, tagged
with a `Feature: candidate-data-transformer, Property {n}` comment) and **pytest** for example,
adapter, CLI, logging, and integration tests. Libraries: `phonenumbers`, `pycountry`, `rapidfuzz`,
`pdfplumber`, `python-docx`, and stdlib `uuid`.

## Tasks

- [x] 1. Set up project scaffold and dependencies
  - [x] 1.1 Create the package layout and configure tooling
    - Create the engine package (e.g. `candidate_transformer/` with `engine/`, `adapters/`,
      `normalizers/`, and a separate `cli/` module) so the engine has zero dependency on the CLI
    - Add `pyproject.toml` (or `requirements.txt`) pinning `phonenumbers`, `pycountry`, `rapidfuzz`,
      `pdfplumber`, `python-docx`, `hypothesis`, and `pytest`
    - Create the `tests/` directory and configure pytest (and a Hypothesis profile with
      `max_examples >= 100`)
    - Add empty `__init__.py` files and a console-script entry point stub for the CLI
    - _Requirements: 13_

- [x] 2. Implement core data models
  - [x] 2.1 Define canonical and intermediate data structures
    - Implement `Canonical_Record` (candidate_id, full_name, emails, phones, location, links,
      headline, years_experience, skills, experience, education, provenance, overall_confidence)
    - Implement `PerSourceRecord` (source_id, source_type, values map of
      `{value, method, normalization_quality}`, errors)
    - Implement `Error_Report` (`{source, stage, error}`), `Log_Entry`
      (`{timestamp, level, module, message}`), and `RunResult` (`{profiles, errors, exit_code}`)
    - Use dataclasses/typed structures; provide construction helpers for all-null records
    - _Requirements: 2.1, 2.2, 6.1, 10.4, 11.1, 11.3_

- [x] 3. Implement value normalization
  - [x] 3.1 Implement phone, date, and country normalizers
    - `normalize_phone` → E.164 via `phonenumbers`, returns `(value|None, quality)`; non-parseable → null
    - `normalize_date` → `YYYY-MM`, quality 1.0 for month+year, ~0.6 for year-only; non-parseable → null
    - `normalize_country` → ISO-3166 alpha-2 via `pycountry`, quality 1.0 exact / ~0.7 fuzzy; unresolvable → null
    - Each returns `(value | None, normalization_quality)` in `[0,1]`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.10_

  - [x] 3.2 Implement the skills normalizer and Controlled_Skill_Vocabulary
    - Define the fixed `Controlled_Skill_Vocabulary` mapping aliases → `Canonical_Skill_Name`
      (e.g. py/python3/"Python Programming" → Python; JS/Javascript/"Java Script" → JavaScript)
    - `normalize_skill` returns canonical name (quality 1.0 exact, ~0.8 alias) or null if out of vocabulary
    - _Requirements: 3.7, 3.8, 3.10_

  - [x] 3.3 Implement the email normalizer
    - `normalize_email` trims surrounding whitespace and lowercases; quality 1.0 valid syntax, ~0.7 otherwise
    - _Requirements: 3.9, 3.10_

  - [x] 3.4 Write property test for normalization canonical-or-null
    - **Property 7: Normalization yields canonical format or null**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

  - [x] 3.5 Write property test for email normalization idempotence
    - **Property 8: Email normalization is idempotent** (`normalize(normalize(e)) == normalize(e)`)
    - **Validates: Requirements 3.9**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement source adapters
  - [x] 5.1 Define the SourceAdapter interface and adapter registry
    - Define the `SourceAdapter` Protocol (`source_type`, `reliability`, `priority`, `can_handle`,
      `ingest`, `extract`) plus `SourceRef` and `RawSource`
    - Implement a fixed-order `AdapterRegistry` whose order encodes default SourcePriority
      (Recruiter CSV, ATS JSON, Resume, LinkedIn, GitHub, Recruiter notes) and a `resolve(ref)` method
    - Define `IngestError` and the per-value extraction-method recording convention
    - _Requirements: 1.1, 1.2, 1.9, 2.5, 7.3_

  - [x] 5.2 Implement the Recruiter CSV adapter (structured)
    - Read CSV with header `name, email, phone, current_company, title`; one `PerSourceRecord` per row
    - Map `current_company` → latest `experience[].company`, `title` → headline/`experience[].title`
    - Record `source_type`/`source_id` and extraction method `csv_column`; absent fields → null
    - _Requirements: 1.3, 2.1, 2.2, 2.4, 2.6, 1.9_

  - [x] 5.3 Implement the ATS JSON adapter (structured)
    - Read JSON with non-canonical keys (e.g. `candidateName`, `emailAddress`, `phoneNumber`, `yrsExp`)
    - Apply a declarative field-mapping table translating ATS keys → canonical paths
    - Record `source_type`/`source_id` and extraction method; absent fields → null
    - _Requirements: 1.4, 2.3, 2.1, 2.2, 2.4, 2.6_

  - [x] 5.4 Implement the Resume and Recruiter notes adapters (unstructured)
    - `ResumeAdapter`: extract text via `pdfplumber`/`python-docx`, then section/regex extractors for
      contact info, experience, education, and skills (method e.g. `pdf_section_skills`)
    - `RecruiterNotesAdapter`: regex extraction of emails, phones, names, mentioned skills from plain text
    - Operate on sample/fetched payloads; absent fields → null, never invented
    - _Requirements: 1.7, 1.8, 2.1, 2.2, 2.4, 2.5, 2.6_

  - [x] 5.5 Implement the GitHub and LinkedIn adapters (unstructured)
    - `GithubAdapter`: from a public profile URL/payload, extract username, name, bio → headline,
      location, repo languages → skills, and the profile link
    - `LinkedinAdapter`: extract name, headline, location, experience, education, skills, and the profile link
    - Operate on fetched/sample profile payloads behind the same interface; record extraction method
    - _Requirements: 1.5, 1.6, 2.1, 2.2, 2.4, 2.5, 2.6_

  - [x] 5.6 Write per-adapter example unit tests
    - For each adapter, a known CSV/JSON/PDF/DOCX/URL-payload fixture yields the expected
      `PerSourceRecord` with correct `source_type`/`source_id` and mapped fields
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.5_

- [x] 6. Implement identity resolution
  - [x] 6.1 Implement Identity_Match_Priority grouping with union-find
    - Apply the five rules in fixed order, stopping at the first satisfied rule: exact email, exact
      phone, email+name, phone+name, name similarity > 0.9 (RapidFuzz ratio)
    - Group with union-find for transitivity; sort records by a stable key for order-independence
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 6.2 Implement deterministic candidate_id assignment
    - Choose a normalized identity key deterministically (smallest normalized email, else smallest
      phone, else normalized-name key); derive `UUID5(NAMESPACE_CANDIDATE, identity_key)`
    - Assign one candidate_id per group; identical content → identical id
    - _Requirements: 4.6, 4.7, 4.8_

  - [x] 6.3 Write property test for identity grouping
    - **Property 9: Identity grouping is order-independent and consistent**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

  - [x] 6.4 Write property test for candidate_id determinism
    - **Property 10: candidate_id is deterministic and idempotent**
    - **Validates: Requirements 4.6, 4.7, 4.8**

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement merge and conflict resolution
  - [x] 8.1 Implement the Winner_Selection_Policy comparator
    - Ordered comparator for single-valued fields: SourcePriority → Field_Confidence →
      Normalization_Quality → stable lexical order of the value's string form
    - Select the first element after sorting; order-independent of input
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 8.2 Implement list-value dedup and provenance tracking
    - Combine list-valued fields (emails, phones, skills, links.other) across sources, dedup, and
      apply a deterministic sort for stable ordering
    - Record a provenance entry `{field, value, source, method, confidence}` for every value, one per
      contributing list value, and a `value=null` "not found" entry for fields no source provided
    - _Requirements: 5.6, 6.1, 6.2, 6.3, 6.4, 12.2, 12.3_

  - [x] 8.3 Write property test for winner selection
    - **Property 11: Winner selection follows the policy deterministically**
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5**

  - [x] 8.4 Write property test for list dedup
    - **Property 12: List-valued fields are deduplicated**
    - **Validates: Requirements 5.6**

  - [x] 8.5 Write property test for provenance completeness
    - **Property 13: Provenance completeness and null explanation**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 17.1, 17.2**

- [x] 9. Implement confidence scoring
  - [x] 9.1 Implement the confidence model
    - `Confidence_Formula`: `clamp(0.5*reliability + 0.3*agreement + 0.2*quality, 0, 1)` per field
    - `Agreement_Score` = (sources supplying winning value)/(sources containing field), 0.0 when none
    - Null fields → Field_Confidence 0.0; `Overall_Confidence` = mean of non-null field confidences (0.0 if all null)
    - Wire scoring into the merge output so each canonical value carries its Field_Confidence
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 9.2 Write property test for the confidence formula
    - **Property 4: Confidence formula correctness**
    - **Validates: Requirements 7.2, 7.4**

  - [x] 9.3 Write property test for confidence bounds and null-honesty
    - **Property 5: Confidence bounds and null-honesty of confidence**
    - **Validates: Requirements 3.10, 7.1, 7.6, 7.7**

  - [x] 9.4 Write property test for agreement monotonicity
    - **Property 6: Agreement monotonicity**
    - **Validates: Requirements 7.5**

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement the projection engine
  - [x] 11.1 Implement the canonical path grammar parser and resolver
    - Parse the path grammar: nested (`location.city`), indexed (`phones[0]`), array projection
      (`skills[].name`)
    - Resolve against the canonical record, distinguishing absent field vs out-of-range index vs
      non-existent subfield
    - _Requirements: 8.5, 8.6, 8.7_

  - [x] 11.2 Implement config-driven projection
    - Read each `Projection_Config` field (`name`, `from`), select subset, rename/remap, apply
      per-field `normalize`, honor `include_provenance`/`include_confidence` toggles
    - Apply `on_missing` (null/omit/error) for absent fields and report projection errors for invalid
      paths; write to a fresh output object, never mutating the canonical record
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.8, 8.9, 8.10, 8.11, 8.12, 8.13, 8.14, 8.15, 8.16, 15.1, 15.3_

  - [x] 11.3 Write property test for projection path resolution
    - **Property 14: Projection path resolution is correct**
    - **Validates: Requirements 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.10, 8.11, 8.12**

  - [x] 11.4 Write property test for missing-value and invalid-path handling
    - **Property 15: Missing-value and invalid-path handling**
    - **Validates: Requirements 8.9, 8.13, 8.14, 8.15, 8.16**

  - [x] 11.5 Write property test for projection isolation
    - **Property 3: Projection isolation (no mutation)**
    - **Validates: Requirements 8.2, 15.1, 15.2, 15.3**

- [x] 12. Implement output schema validation
  - [x] 12.1 Implement the Validation_Module
    - Validate the `Projected_Profile` against the inline schema: type, required, enum, array
      (list + element type), and object (declared subfields + types)
    - Produce a structured validation error naming the failing field and reason; decoupled from projection
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [x] 12.2 Write property test for schema validation soundness
    - **Property 16: Schema validation soundness**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7**

- [x] 13. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Implement the TransformerEngine orchestration
  - [x] 14.1 Implement `TransformerEngine.run(refs, config)`
    - Wire all stages: ingest via registry → extract → normalize → resolve identity → merge → score →
      project → validate, producing one Projected_Profile per identity group
    - Enforce per-candidate isolation (group boundary catches failures into an Error_Report and
      continues), all-null record when every source fails, and structured INFO/WARNING/ERROR logging
    - Guarantee no exception escapes; always return a `RunResult` with `profiles`, `errors`, `exit_code`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.4, 11.5, 11.6, 12.1, 14.1, 14.3, 14.4_

  - [x] 14.2 Write property test for robustness
    - **Property 2: Robustness — never crashes on garbage**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 18.1**

  - [x] 14.3 Write property test for end-to-end determinism
    - **Property 1: End-to-end determinism**
    - **Validates: Requirements 7.8, 12.1, 12.2, 12.3, 16.1**

  - [x] 14.4 Write property test for per-candidate batch isolation
    - **Property 17: Per-candidate batch isolation**
    - **Validates: Requirements 14.1, 14.3, 14.4**

  - [x] 14.5 Write logging-level unit tests
    - Assert INFO/WARNING/ERROR emission for representative events (progress, missing/empty values, errors)
    - _Requirements: 11.4, 11.5, 11.6_

  - [x] 14.6 Write scale/memory integration test
    - One large-batch run (thousands of synthetic candidates) asserting completion and bounded memory
    - _Requirements: 14.2, 19.1_

- [x] 15. Implement the command-line interface
  - [x] 15.1 Implement the thin CLI over `TransformerEngine.run`
    - Accept one or more `--input` references and a `--config`; emit JSON; `--output PATH` writes file,
      absence prints to stdout
    - Missing required `--input` → usage error; unparseable config → configuration error
    - Exit 0 on clean run; non-zero when any source/projection error occurred, printing Error_Reports to stderr
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 15.2 Write CLI tests
    - Invoke with/without `--output`, with a missing `--input`, and with an unparseable config; assert
      exit codes, stdout vs file output, and error messages
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

- [x] 16. Create fixtures, configs, example output, and README
  - [x] 16.1 Create sample input fixtures
    - Add sample Recruiter CSV, ATS JSON, resume (PDF/DOCX or text), and recruiter notes files that
      together describe overlapping candidates exercising identity matching
    - _Requirements: 1.2, 1.3, 1.4, 1.7, 1.8_

  - [x] 16.2 Create example projection configs
    - Add a default-schema `Projection_Config` (matching the design example) and at least one custom
      config selecting a different field subset, renames, and `on_missing` behaviors
    - _Requirements: 8.1, 8.3, 8.4, 8.8, 8.13, 8.14, 8.15_

  - [x] 16.3 Create an end-to-end integration test that produces example output
    - Run the engine on the sample fixtures + both configs, assert profiles/errors are produced, and
      write the resulting JSON to an example output file checked by the test
    - _Requirements: 13.1, 13.2, 14.1, 12.1_

  - [x] 16.4 Write the README with exact run steps
    - Document install, the exact CLI invocation (`--input`/`--config`/`--output`), expected exit
      codes, and how to run the test suite
    - _Requirements: 13.1, 13.2, 13.3_

- [x] 17. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core
  implementation tasks are never optional.
- Each of the 17 correctness properties maps to exactly one Hypothesis property-based test
  (minimum 100 iterations), tagged with a `Feature: candidate-data-transformer, Property {n}` comment.
- CLI, logging, adapter, and scale tests are example/integration tests, not properties.
- Each task references specific requirement clauses and/or properties for traceability.
- Checkpoints provide incremental validation at natural pipeline boundaries.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "16.1", "16.2"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "5.1"] },
    { "id": 3, "tasks": ["3.4", "3.5", "5.2", "5.3", "5.4", "5.5"] },
    { "id": 4, "tasks": ["5.6", "6.1", "9.1"] },
    { "id": 5, "tasks": ["6.2", "8.1", "9.2", "9.3", "9.4", "11.1"] },
    { "id": 6, "tasks": ["6.3", "6.4", "8.2", "11.2"] },
    { "id": 7, "tasks": ["8.3", "8.4", "8.5", "11.3", "11.4", "11.5", "12.1"] },
    { "id": 8, "tasks": ["12.2", "14.1"] },
    { "id": 9, "tasks": ["14.2", "14.3", "14.4", "14.5", "15.1"] },
    { "id": 10, "tasks": ["14.6", "15.2", "16.3"] },
    { "id": 11, "tasks": ["16.4"] }
  ]
}
```

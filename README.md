# Multi-Source Candidate Data Transformer

A deterministic, explainable pipeline that ingests candidate data from many sources
(Recruiter CSV, ATS JSON, Resume PDF/DOCX, Recruiter notes, GitHub, LinkedIn),
normalizes and merges them into **one canonical record per candidate** with full
provenance and confidence, then projects that record into **any caller-requested
JSON schema** at runtime.

> **Guiding principle:** *a wrong-but-confident value is worse than an
> honestly-empty one.* The system never invents data — an unknown value becomes
> `null` (or is preserved in `unknown_skills`), and it never crashes on a missing or
> malformed source.

---

## Table of Contents

- [1. The Problem](#1-the-problem)
- [2. Architecture](#2-architecture)
- [3. Key Design Decisions](#3-key-design-decisions)
- [4. Quick Start](#4-quick-start)
- [5. Important Commands](#5-important-commands)
- [6. The Canonical Schema](#6-the-canonical-schema)
- [7. Projection Configs](#7-projection-configs)
- [8. Testing](#8-testing)
- [9. Known Limitations & Future Work](#9-known-limitations--future-work)
- [10. Repository Layout](#10-repository-layout)

---

## 1. The Problem

Candidate data arrives from many places at once. Downstream products need one clean
profile per candidate: a fixed set of fields, normalized formats, deduplicated across
sources, with a record of **where each value came from** and **how confident** we are
in it. Sources may be missing, empty, or malformed, and the same person can appear in
several sources with conflicting values.

This project builds that transformer, plus a **runtime projection layer** so the same
canonical record can be reshaped into different output schemas (e.g. an HR view vs. a
technical view) with no code change.

---

## 2. Architecture

An eight-stage pipeline. Everything **before** the canonical record is *cleaning*;
everything **after** is *presentation*.

```text
Recruiter CSV   ATS JSON   Resume PDF/DOCX   Notes   GitHub   LinkedIn
      └────────────┴──────────┬──────────────┴─────────┴────────┘
                              ▼
                       Source Adapters            (1) Ingest
                              ▼                    (2) Extract   → PerSourceRecord
                       Normalizers                (3) Normalize  (phone/date/country/email/skills)
                              ▼
                     Identity Resolver            (4) Resolve identity (who is the same person?)
                              ▼
                  Merge + Conflict Resolution      (5) Merge      (Source Priority + agreement)
                              ▼                    (6) Confidence (per-field + overall) + provenance
                  CANONICAL CANDIDATE RECORD
                              ▼
                     Projection Layer             (7) Project to requested schema
                              ▼                    (8) Validate against the schema
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   HR projection      Technical projection    Any custom config
```

**Guarantees**

- **Deterministic** — same inputs produce byte-identical output. No wall-clock or
  randomness feeds the result; list fields are deduped and sorted; identity grouping
  is order-independent.
- **Explainable** — every field carries a provenance entry
  `{field, value, source, method, confidence}`. Fields no source supplied get an
  honest `not_found` entry.
- **Robust** — a missing or garbage source becomes a structured error and the run
  continues; the engine always returns a result and never crashes.

---

## 3. Key Design Decisions

### Source Priority

Used to break merge conflicts, most authoritative first:

```text
Recruiter CSV  >  ATS JSON  >  Resume  >  LinkedIn  >  GitHub  >  Recruiter notes
```

### Confidence Formula

Per field, clamped to `[0, 1]`:

```text
Field_Confidence = clamp(0.5 * Source_Reliability
                       + 0.3 * Agreement
                       + 0.2 * Normalization_Quality)
```

`Overall_Confidence` is the mean of the present (non-null) fields' confidences. More
agreeing sources never lowers confidence.

### Identity Resolution

Match keys are applied in priority order:

1. Exact email → merge
2. Exact phone → merge
3. Full-name similarity > 0.9 (RapidFuzz) → merge **only if no email/phone contradicts it**

Rule 3 is deliberately guarded: a shared name is the weakest signal, so two people who
share a name but have **different email or phone are kept separate**. A shared strong
identifier (email *or* phone) is enough to merge; a conflicting one blocks a name-only
merge. `candidate_id` is a deterministic `UUID5` of the group's normalized identity key.

### Normalization

| Field | Rule |
|-------|------|
| Phones | E.164 (validated, not just reformatted — invalid numbers become `null`) |
| Dates | `YYYY-MM` |
| Country | ISO-3166 alpha-2 |
| Skills | canonical names via layered matcher: exact → alias → fuzzy → `unknown_skills` (kept, never dropped) |

The skills vocabulary lives in `config/skills.json` and is editable with **no code change**.

---

## 4. Quick Start

Requires **Python 3.11**. A virtual environment ships at `.venv`; on Windows run
everything with `.venv\Scripts\python.exe`.

```powershell
# From the repo root. If .venv does not exist yet:
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
```

After install, the console script `candidate-transform` is equivalent to
`python -m candidate_transformer.cli.main`.

### CLI Options

| Option | Required | Meaning |
|--------|:--------:|---------|
| `--input` | yes | A source file/URL. Repeat once per source. Optional `type=path` prefix forces an adapter (e.g. `github=path`, `resume=path`). |
| `--config` | yes | Projection config JSON. **Repeat** to project the same record into several schemas in one run. |
| `--output` | no | Write JSON to this file. Omit to print to stdout. |
| `--show-canonical` | no | Also print the full canonical record, then each projection, as separate labelled sections. |

Adapter routing by extension: `.csv` → Recruiter CSV, `.json` → ATS JSON,
`.pdf` / `.docx` → Resume, `.txt` → Recruiter notes.

**Exit codes:** `0` clean · `2` usage/config error · `1` completed with source/projection errors (reported to stderr).

---

## 5. Important Commands

All commands run from the repo root. The `input/` folder holds a real resume
(`NeerajKalkoor.pdf`) plus a Recruiter CSV and ATS JSON for the same candidate.

### 5.1 Merge once, project many

Builds the canonical record a single time, then projects it into an HR view and a
technical view, shown as separate sections:

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input input\neeraj_recruiter.csv `
  --input input\neeraj_ats.json `
  --input input\NeerajKalkoor.pdf `
  --config samples\configs\hr_projection.json `
  --config samples\configs\technical_projection.json `
  --show-canonical
```

### 5.2 Full canonical schema (all fields + provenance + confidence)

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input input\neeraj_recruiter.csv `
  --input input\neeraj_ats.json `
  --input input\NeerajKalkoor.pdf `
  --config samples\configs\full_default.json
```

### 5.3 Robustness — a broken source does not stop the run

The corrupt PDF yields an ingest error (printed to stderr) while the CSV + ATS still
produce a profile:

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input input\neeraj_recruiter.csv `
  --input input\neeraj_ats.json `
  --input input\broken_resume.pdf `
  --config samples\configs\hr_projection.json
```

### 5.4 Identity — same name, different people are NOT merged

A Recruiter CSV and an ATS JSON with the same name but different email/phone stay as
two separate candidates:

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input input\identity\person_one.csv `
  --input input\identity\person_two_ats.json `
  --config samples\configs\default.json
```

### 5.5 Run the provided sample fixtures

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input samples\recruiter.csv `
  --input samples\ats.json `
  --input samples\resume_jane_doe.docx `
  --input samples\notes.txt `
  --config samples\configs\default.json
```

### 5.6 Write output to a file

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input input\neeraj_recruiter.csv `
  --input input\neeraj_ats.json `
  --input input\NeerajKalkoor.pdf `
  --config samples\configs\full_default.json `
  --output out.json
```

### 5.7 Spot-check a normalizer

```powershell
.venv\Scripts\python.exe -c "from candidate_transformer.normalizers import normalize_phone, normalize_skill; print(normalize_phone('+91 78928 86596')); print(normalize_skill('Javscript'))"
```

---

## 6. The Canonical Schema

| Field | Type | Notes |
|-------|------|-------|
| `candidate_id` | string | deterministic UUID5 of the identity key |
| `full_name` | string | |
| `emails` | string[] | deduped across sources |
| `phones` | string[] | E.164 |
| `location` | `{city, region, country}` | country is ISO-3166 alpha-2 |
| `links` | `{linkedin, github, portfolio, other[]}` | |
| `headline` | string \| null | |
| `years_experience` | number \| null | |
| `skills` | `[{name, confidence, sources[]}]` | canonical skill names |
| `unknown_skills` | string[] | out-of-vocabulary skills, kept not dropped |
| `experience` | `[{company, title, start, end, summary}]` | dates as YYYY-MM |
| `education` | `[{institution, degree, field, end_year}]` | |
| `provenance` | `[{field, value, source, method, confidence}]` | where each value came from |
| `overall_confidence` | number | mean of present fields' confidences |

---

## 7. Projection Configs

A projection config reshapes the output **at runtime, with no code change**. Each
field entry can:

- **Rename** a field (`name`/`path` + `from`)
- **Pull a single element** from a list (`emails[0]`)
- **Pluck from a list of objects** (`skills[].name`)
- **Reach nested paths** (`location.city`)
- **Set per-field normalization** (`E164`, `canonical`, `lowercase`)
- Mark `required`, restrict by `enum`, and choose `on_missing` behaviour (`null` / `omit` / `error`)

Provenance and confidence are toggled globally with `include_provenance` /
`include_confidence`.

### Ready-made configs (`samples/configs/`)

| Config | Shape |
|--------|-------|
| `full_default.json` | the complete canonical schema + provenance + confidence |
| `default.json` | curated default view (id, name, email, phone, skills, experience, confidence) |
| `hr_projection.json` | contact-focused (name, email, phone, experience, location) |
| `technical_projection.json` | skills-focused (id, name, skills, github, confidence) |
| `assignment_example.json` | the assignment's exact example config dialect |
| `custom.json`, `custom_minimal.json` | alternate views |

Both config dialects are supported: `name` + `from` (ours) and `path` + `from` (the
assignment's).

---

## 8. Testing

```powershell
# Full suite (399 tests, including Hypothesis property-based tests)
.venv\Scripts\python.exe -m pytest

# Quiet
.venv\Scripts\python.exe -m pytest -q

# A single file or a single test
.venv\Scripts\python.exe -m pytest tests\test_identity_resolver.py
.venv\Scripts\python.exe -m pytest tests\test_confidence_model.py -k overall
```

The suite mixes example-based unit/integration tests with **property-based tests**
that assert the core invariants across many generated inputs: determinism, robustness
on arbitrary/garbage input, order-independent identity grouping, confidence bounds,
and provenance completeness.

---

## 9. Known Limitations & Future Work

- **Identity matching is O(n²)** — fine for thousands of candidates (the target
  scale); beyond ~10k it needs blocking/indexing (hash-join on exact keys +
  phonetic/prefix blocking for fuzzy names).
- **Extraction is deterministic heuristics** (regex, keyword lists, ATS field map,
  section headers) — good precision, limited recall on unfamiliar layouts. It degrades
  gracefully to `null` rather than guessing. A production system would add
  ML/layout-aware extraction, at the cost of determinism.
- **DOCX hyperlink URLs** that aren't visible text aren't extracted (PDF hyperlinks are).
- Some heuristics (ATS field map, resume section/keyword lists) are still hardcoded;
  they could be externalized to `config/` like the skills dictionary already is.

---

## 10. Repository Layout

| Path | What it is |
|------|------------|
| `candidate_transformer/models/` | data models (canonical record, per-source record, provenance, run result, serialization) |
| `candidate_transformer/normalizers/` | phone, date, country, email, skills normalizers |
| `candidate_transformer/adapters/` | one adapter per source type + registry + text helpers |
| `candidate_transformer/engine/` | identity, merge, confidence, projection, validation, and the orchestrator (`transformer.py`) |
| `candidate_transformer/cli/` | the `candidate-transform` CLI |
| `config/skills.json` | editable skills dictionary (canonical → aliases) |
| `samples/` | sample input fixtures and projection configs |
| `input/` | real-resume demo dataset (CSV + ATS + PDF, plus identity examples) |
| `tests/` | pytest + Hypothesis suite |
| `commands.md` | quick command reference |

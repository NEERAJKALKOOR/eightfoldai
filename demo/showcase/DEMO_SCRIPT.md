# Demo Script — Multi-Source Candidate Data Transformer

A ~5-6 minute walkthrough that proves the architecture with 5 edge cases.
All commands are PowerShell, run from the repo root. One candidate ("John Rivera")
appears in a Recruiter CSV, an ATS JSON export, and a Resume — the three sources
share an email, so the identity resolver merges them into one record.

Dataset: `demo/showcase/` (john.csv, john_ats.json, john_resume.txt, broken_resume.pdf)

---

## 0. Architecture (talk only, ~1 min)

> "Messy sources → one canonical record → projected to any requested schema.
> Everything before the canonical record is cleaning; everything after is
> presentation. The guiding rule: a wrong-but-confident value is worse than an
> honestly-empty one — we never invent."

Pipeline: Ingest → Extract → Normalize → Resolve Identity → Merge → Confidence → Project → Validate.

---

## 1. Normal candidate + full canonical view (~1 min)

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\showcase\john.csv `
  --input demo\showcase\john_ats.json `
  --input "resume=demo\showcase\john_resume.txt" `
  --config samples\configs\full_default.json
```

Point at:
- Three sources merged into ONE record (`candidate_id`, one `full_name`, deduped emails/phones).
- `provenance` array — every value traces to a source + method + confidence.
- `overall_confidence` at the bottom (~0.93).

---

## 2. Conflicting values → winner + agreement (~1 min)  ⭐ most important

Same run as above. Scroll to `headline` and its provenance.

- `headline` = **"Staff Engineer"**. The Resume said "Software Engineer"; the CSV
  and ATS said "Staff Engineer".
- Winner chosen by **Source Priority** (Recruiter CSV outranks Resume).
- Its confidence (**0.875**) is boosted by **agreement** — 2 of 3 sources agreed.
- Show `emails`: all THREE sources supplied the same address, so provenance lists
  three entries — that's the agreement signal made visible.

> "Conflicts are resolved deterministically: highest-priority source wins, and
> agreement across sources raises confidence. Every choice is auditable in provenance."

---

## 3. Messy resume extraction (~45s)

Same output, scroll to `skills` and `education`.

- The resume's skills section had glued category labels:
  `Backend: Java, Spring Boot` / `Frontend: React.js, HTML, CSS`.
- Output is clean canonical skills: `Java`, `Spring Boot`, `React`, `HTML`, `CSS`
  (labels stripped, `React.js` → `React`).
- `education` contains only **Stanford University** — the parser **stops at the
  `PROJECTS` section**, so project bullets never leak in as fake schools.

> "Extraction is deterministic heuristics — good precision. It strips noise and
> stops at section boundaries rather than over-capturing."

---

## 4. Unknown skills — honest, not invented (~30s)

Same output, look at `unknown_skills`.

```json
"skills": ["CSS", "Docker", "HTML", "Java", "PostgreSQL", "Python", "React", "Spring Boot"],
"unknown_skills": ["CoolFramework3000"]
```

> "`CoolFramework3000` isn't in our vocabulary. We don't guess a canonical name and
> we don't drop it — we preserve it separately in `unknown_skills`. A later
> vocabulary update can promote it with no code change."

---

## 5. Broken / missing source → graceful degradation (~45s)

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\showcase\john.csv `
  --input demo\showcase\john_ats.json `
  --input demo\showcase\broken_resume.pdf `
  --config samples\configs\hr_projection.json
```

Point at:
- An `error_report` (ingest stage) is printed to **stderr** for the corrupt PDF.
- The profile is **still generated** from CSV + ATS. The run does not crash.
- `location` is now `null` (it lived only in the resume) — honestly empty, not invented.

> "A garbage source becomes a structured error and the run continues. Robustness is
> a hard requirement — the engine never crashes and never fabricates."

---

## 6. Runtime projection switching (~1 min)  ⭐ key architectural feature

```powershell
# HR view
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\showcase\john.csv --input demo\showcase\john_ats.json `
  --input "resume=demo\showcase\john_resume.txt" `
  --config samples\configs\hr_projection.json

# Technical view
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\showcase\john.csv --input demo\showcase\john_ats.json `
  --input "resume=demo\showcase\john_resume.txt" `
  --config samples\configs\technical_projection.json
```

- HR: `name`, `email`, `phone`, `experience`, `location` — no provenance/confidence.
- Technical: `candidate_id`, `name`, `skills`, `github`, confidence.

> "Same canonical record, same engine, no code change — just a different config.
> The projection layer reshapes, renames, and selects fields at runtime."

---

## 7. Tests (~15s)

```powershell
.venv\Scripts\python.exe -m pytest -q
```

> "396 tests, including property-based tests for determinism, robustness on random
> garbage input, and order-independent identity grouping."

---

## If you only have time for 3

1. **Conflict (section 2)** — merge policy + confidence + provenance.
2. **Messy resume + unknown skills (sections 3-4)** — extraction quality + honest-null philosophy.
3. **Projection switching (section 6)** — the key architectural requirement.

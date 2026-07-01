# Demo Script (Real Data) — Multi-Source Candidate Data Transformer

Uses a **real resume** (`demo/NeerajKalkoor.pdf`) as the unstructured source, paired
with a realistic Recruiter CSV and ATS JSON export for the same candidate. In a real
pipeline the resume comes from the candidate while the CSV/ATS come from other
systems — so only the structured sources are authored here; the resume is genuine.

All commands are PowerShell, run from the repo root. The three sources share the
candidate's real email, so the identity resolver merges them into one record.

Sources:
- `demo/NeerajKalkoor.pdf`        — real resume (unstructured)
- `demo/real/neeraj_recruiter.csv` — recruiter CSV (structured)
- `demo/real/neeraj_ats.json`      — ATS export (structured, non-canonical field names)
- `demo/real/corrupt_resume.pdf`   — deliberately corrupt, for the robustness demo

---

## 0. Architecture (talk only, ~1 min)

> "Messy sources → one canonical record → projected to any requested schema.
> Guiding rule: a wrong-but-confident value is worse than an honestly-empty one — we
> never invent."

Pipeline: Ingest → Extract → Normalize → Resolve Identity → Merge → Confidence → Project → Validate.

---

## 1. Merge three real sources into one record (~1 min)

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\real\neeraj_recruiter.csv `
  --input demo\real\neeraj_ats.json `
  --input demo\NeerajKalkoor.pdf `
  --config samples\configs\full_default.json
```

Point at:
- ONE record — the three sources were matched on the shared email and merged.
- `provenance` — every value traces to a source + method + confidence.
- `overall_confidence` ≈ 0.93.

---

## 2. Conflicting values → winner + agreement (~1 min)  ⭐ most important

The conflict field is **`full_name`** — all three sources supply it with different
priorities, so the winner is genuinely decided by Source Priority.

The three sources disagree:
- Resume  → `"T Neeraj Kalkoor"`  (stray "T" from PDF extraction; lowest priority)
- Recruiter CSV → `"Neeraj Kalkoor"`  (highest priority)
- ATS     → `"Neeraj Kalkoor"`  (agrees with CSV)

In the output:
- `full_name` = **"Neeraj Kalkoor"** — the winner. Look at its `provenance` entry:
  `source: neeraj_recruiter.csv`, `confidence: 0.875`.
- **Why it won:** Source Priority — Recruiter CSV outranks the ATS and the resume.
- **Why 0.875:** agreement — 2 of the 3 sources supplied this value; the resume's
  "T Neeraj Kalkoor" lost. So the pipeline *also corrected a messy extraction*.
- Agreement is visible in `skills` too: `React` and `Node.js` came from **two
  sources** (resume + ATS) → confidence **0.95**; `MongoDB` (ATS only) → **0.80**.

> "Conflicts resolve deterministically — the highest-priority source wins, agreement
> across sources raises confidence, and the loser is dropped but every choice stays
> auditable in provenance. Here it even cleaned up the resume's stray 'T'."

---

## 3. Messy real-resume extraction (~45s)

Same output, look at `skills` and `education`.

- The real resume lists skills in a `Backend:` / `Frontend:` style block; the adapter
  strips those labels and canonicalizes: `Java`, `React`, `Node.js`, `Tailwind CSS`,
  `PostgreSQL`, etc. — all cleaned automatically.
- `education` has the two real institutions and **stops correctly** (no project or
  experience bullets leak in). `degree`/`end_year` are honest `null` where the PDF
  layout doesn't reliably pair them — empty over wrong.

> "Real resume, real noise. Deterministic heuristics clean it and stop at section
> boundaries rather than over-capturing."

---

## 4. Unknown skills — preserved, not invented (~30s)

Same output, look at `unknown_skills`.

- The ATS carried a free-text tag `CoolFramework3000` that isn't in our vocabulary.
- It is **not** guessed into a canonical name and **not** dropped — it's kept in
  `unknown_skills`. A later vocabulary update can promote it with no code change.

---

## 5. Broken / missing source → graceful degradation (~45s)

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\real\neeraj_recruiter.csv `
  --input demo\real\neeraj_ats.json `
  --input demo\real\corrupt_resume.pdf `
  --config samples\configs\hr_projection.json
```

Point at:
- An `error_report` (ingest stage) printed to **stderr** for the corrupt PDF.
- The profile is **still generated** from CSV + ATS — no crash.

> "A garbage source becomes a structured error and the run continues. The engine
> never crashes and never fabricates."

---

## 6. Runtime projection switching — merge once, project many (~1 min)  ⭐ key architectural feature

One command, one pipeline run. Extraction, normalization, and merge happen **once**;
the shared canonical record is then projected into BOTH configs:

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main `
  --input demo\real\neeraj_recruiter.csv --input demo\real\neeraj_ats.json `
  --input demo\NeerajKalkoor.pdf `
  --config samples\configs\hr_projection.json `
  --config samples\configs\technical_projection.json
```

Output is a JSON object keyed by config filename:
- `hr_projection.json` → `name`, `email`, `phone`, `experience`, `location` (no provenance/confidence).
- `technical_projection.json` → `candidate_id`, `name`, `skills`, `github`, confidence.

> "Same canonical record, same single run — the pipeline extracts and merges once,
> then projects into each requested schema. No code change, no re-processing. That's
> the clean separation between the canonical record and the projection layer."

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
2. **Messy real resume + unknown skills (sections 3-4)** — extraction quality + honest-null philosophy.
3. **Projection switching (section 6)** — the key architectural requirement.

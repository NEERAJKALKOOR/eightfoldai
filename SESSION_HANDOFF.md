# Session Handoff — Multi-Source Candidate Data Transformer

Use this to resume work in a fresh session. It captures the project state, what was
built, key decisions, and open items.

## Project summary
A deterministic, explainable pipeline that ingests candidate data from multiple
sources (Recruiter CSV, ATS JSON, Resume PDF/DOCX, Recruiter notes, GitHub,
LinkedIn), normalizes + merges into one canonical record with provenance and
confidence, then projects it into any caller-requested JSON schema. This is the
Eightfold Engineering Intern assignment.

- **Language/env:** Python 3.11, virtualenv at `.venv` (run everything with `.venv\Scripts\python.exe`).
- **Status:** Implementation complete. **386 tests pass** (`.venv\Scripts\python.exe -m pytest`).
- **Guiding principle:** "a wrong-but-confident value is worse than an honestly-empty one" — never invent; unknown → null (or `unknown_skills`); never crash.

## Architecture (file map)
- `candidate_transformer/models/` — `canonical.py` (CanonicalRecord + nested types + `unknown_skills`), `per_source.py` (PerSourceRecord/FieldValue), `reporting.py` (ErrorReport/LogEntry), `run_result.py` (RunResult), `serialization.py` (to_dict).
- `candidate_transformer/normalizers/` — `phone.py` (E.164), `date.py` (YYYY-MM), `country.py` (ISO-3166), `email.py`, `skills.py` (loads `config/skills.json`, exact→alias→fuzzy), `common.py`.
- `candidate_transformer/adapters/` — `base.py` (SourceAdapter protocol, SourceRef/RawSource, SOURCE_PRIORITY/RELIABILITY), `registry.py`, `recruiter_csv.py`, `ats_json.py`, `resume.py`, `recruiter_notes.py`, `github.py`, `linkedin.py`, `_text_extract.py`.
- `candidate_transformer/engine/` — `identity.py` (group + candidate_id, union-find), `merge.py` (select_winner + combine_list_field + provenance), `confidence.py`, `path_resolver.py`, `projection.py`, `validation.py`, `transformer.py` (**orchestrator: TransformerEngine.run**).
- `candidate_transformer/cli/` — `main.py` (CLI), `fetch_github.py` (opt-in live GitHub fetch).
- `config/skills.json` — external skills dictionary (~65 skills, editable, no code change).
- `samples/configs/` — projection configs (see below).
- `tests/` — pytest + Hypothesis (17 correctness properties + unit/integration).
- Docs: `README.md`, `commands.md`, `TECHNICAL_DESIGN.md` (one-pager).

## Pipeline (8 stages)
Ingest → Extract → Normalize → Resolve Identity → Merge → Confidence → Project → Validate.
Everything before the CanonicalRecord = cleaning; everything after = presentation.

## Key decisions / policies (hardcoded on purpose)
- **SourcePriority:** Recruiter CSV > ATS JSON > Resume > LinkedIn > GitHub > Recruiter notes.
- **Source_Reliability:** 0.95 / 0.90 / 0.85 / 0.80 / 0.70 / 0.60.
- **Confidence formula:** `clamp(0.5*reliability + 0.3*agreement + 0.2*quality, 0, 1)`; null fields → 0.0; overall = mean of non-null.
- **Identity match (first hit wins):** exact email → exact phone → email+name → phone+name → name similarity > 0.9 (RapidFuzz). Union-find for transitivity, order-independent. `candidate_id = UUID5(fixed namespace, identity key)`.
- **Skills:** layered exact (1.0) → alias (0.8) → fuzzy (0.6, threshold 90) → `unknown_skills` (kept, not dropped).

## Projection configs (`samples/configs/`)
- `full_default.json` — the complete canonical schema (all 13 fields + provenance + confidence).
- `default.json` — curated default view.
- `custom.json`, `custom_minimal.json` — alternate views.
- `assignment_example.json` — the assignment's exact example config dialect.
- `hr_projection.json` — contact + experience view.
- `technical_projection.json` — skills + github + confidence view.

Config supports BOTH our dialect and the assignment's: `name`/`path` (output field), `from` (source path, defaults to name), nested/indexed/array paths (`location.city`, `phones[0]`, `skills[].name`), `type` (incl. `string[]`), `required`, `enum`, `normalize` (`lowercase`/`uppercase`/`E164`/`canonical`), `element_type`, per-field + global `on_missing` (null/omit/error), `include_provenance`/`include_confidence` toggles.

## What was changed/added THIS session (beyond the base spec build)
1. **Skills made configurable + layered** — external `config/skills.json`, fuzzy matching, `unknown_skills` bucket on CanonicalRecord (was a tiny hardcoded vocab that dropped unknowns).
2. **Resume skills extraction** — strips glued `Category:` labels (`Backend: Java` → `Java`), drops junk tokens.
3. **Config compatibility** — projection now accepts the assignment's exact format (`path`, optional `from`, global `on_missing`, `string[]`, `normalize: E164/canonical`).
4. **Full default schema config** — `samples/configs/full_default.json`.
5. **Resume education/location fix** — education stops at next section + requires institution/degree signal (was 25 junk entries → 2 real schools); location rejects sentence-like prose (→ honest null).
6. **CLI typed inputs** — `--input "github=path"` / `linkedin=` / `ats_json=` etc. to force an adapter (needed because `.json` defaults to ATS).
7. **Live GitHub fetcher** — `python -m candidate_transformer.cli.fetch_github <user-or-url> --out demo\gh.json` (public API, stdlib urllib, opt-in/offline-safe). LinkedIn intentionally NOT fetchable (no public API).
8. **PDF extractor switched to PyMuPDF** (`fitz`) with pdfplumber fallback — preserves line/section structure, fixes the gluing at the source. Added `pymupdf==1.28.0` to `pyproject.toml` (installed in `.venv`).

## How to run (Windows, from repo root)
```powershell
# Full schema output
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\neeraj.csv --input demo\NeerajKalkoor.pdf --config samples\configs\full_default.json --output demo\output_full.json

# HR vs Technical projections (same record, different shapes)
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --input samples\ats.json --input samples\resume_jane_doe.docx --input samples\notes.txt --config samples\configs\hr_projection.json
.venv\Scripts\python.exe -m candidate_transformer.cli.main ... --config samples\configs\technical_projection.json

# Live GitHub → pipeline
.venv\Scripts\python.exe -m candidate_transformer.cli.fetch_github https://github.com/<user> --out demo\gh.json
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input "github=demo\gh.json" --config samples\configs\default.json

# Tests
.venv\Scripts\python.exe -m pytest          # 386 passing
```
See `commands.md` for the full command reference.

## Known limitations / honest gaps (good "future work" talking points)
- **Scalability:** volume-fine on thousands (scale test: 2000 candidates ~18s, ~5 MiB). Bottleneck is **O(n²) identity matching** in `engine/identity.py` — degrades past ~10k. Fix = blocking/indexing (hash-join exact keys + phonetic/prefix blocking for fuzzy names).
- **Extraction coverage:** deterministic heuristics (regex, keyword lists, ATS map, section headers) — good precision, limited recall on unfamiliar formats. Degrades gracefully (null, never wrong). Production fix = ML extraction / layout-aware models, which would break determinism.
- **Hardcoded extraction heuristics** still in code (ATS field map in `ats_json.py`, section headers + education keyword lists in `resume.py`, regexes in `_text_extract.py`). Could be externalized to `config/` JSON like skills was. NOT yet done.
- **Education degree/year** on the demo PDF are `null` (the detail lines are above the institution in a layout we don't reliably pair) — honest nulls, acceptable.

## Open / possible next steps (none in progress)
- [ ] Externalize ATS map + resume section/keyword lists to `config/` JSON (deterministic flexibility).
- [ ] Fuzzy/structural section-header detection (catch glued/variant headers) — low complexity, deterministic.
- [ ] Implement blocking in identity resolver for true scale.
- [ ] Deliverables: one-page design PDF (`<Name>_<Email>_Eightfold.pdf` — content is in `TECHNICAL_DESIGN.md`), ~2-min demo video, push public GitHub repo.

## Demo notes (`demo/` folder — scratch files)
`neeraj.csv`, `NeerajKalkoor.pdf` (real resume), `gh.json` / `github_live.json` (fetched), `ats_demo.json`, `output_full.json`. These are walkthrough artifacts, safe to delete.

## IMPORTANT context for the new session
- This is a **spec-driven** project; spec lives in `.kiro/specs/candidate-data-transformer/` (requirements.md, design.md, tasks.md — all tasks complete).
- Always run Python via `.venv\Scripts\python.exe` (it has hypothesis + all deps).
- Engine is deterministic & explainable by design — keep any change deterministic (no randomness/wall-clock in output; provenance for every value).
- After any code change: run `.venv\Scripts\python.exe -m pytest` and keep it green.

# Commands Reference

All commands are run from the repository root:
`c:\Users\kalko\Desktop\neeraj\intern`

The project ships a virtual environment at `.venv`. On Windows use
`.venv\Scripts\python.exe` to run everything (it has the pinned dependencies and
`hypothesis` installed).

---

## 1. Setup (one time)

```powershell
# Create the virtual environment (only if .venv does not already exist)
python -m venv .venv

# Activate it (PowerShell)
.venv\Scripts\Activate.ps1

# Install the package + test tools (pytest, hypothesis)
pip install -e ".[test]"
```

After `pip install -e .` the console script `candidate-transform` is available
(equivalent to `python -m candidate_transformer.cli.main`).

---

## 2. CLI usage

The CLI takes one or more `--input` sources, one `--config`, and an optional
`--output`.

| Option | Required | Meaning |
|--------|:--------:|---------|
| `--input` | yes | A source file/URL. Repeat once per source. Optional `type=path` prefix forces an adapter. |
| `--config` | yes | Projection config JSON (controls the output shape). |
| `--output` | no | Write JSON to this file. Omit to print to stdout. |

### Show help
```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --help
```

### Run the sample fixtures (print to screen)
```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --input samples\ats.json --input samples\resume_jane_doe.docx --input samples\notes.txt --config samples\configs\default.json
```

### Run and write JSON to a file
```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --input samples\ats.json --config samples\configs\default.json --output profiles.json
```

### Console-script form (after `pip install -e .`)
```powershell
candidate-transform --input samples\recruiter.csv --config samples\configs\default.json
```

---

## 3. Source types and how they are routed

File extension picks the adapter automatically:

| Extension / source | Adapter | Structured? |
|--------------------|---------|-------------|
| `.csv` | Recruiter CSV | structured |
| `.json` | ATS JSON | structured |
| `.pdf` / `.docx` | Resume | unstructured |
| `.txt` | Recruiter notes | unstructured |
| GitHub JSON payload | GitHub | unstructured |
| LinkedIn JSON payload | LinkedIn | unstructured |

GitHub and LinkedIn read a **local JSON payload** (offline, deterministic). Because
a plain `.json` is claimed by the ATS adapter, force the right adapter with a
`type=path` prefix:

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input "github=demo\github_payload.json" --input "linkedin=demo\linkedin_payload.json" --config samples\configs\default.json
```

Valid type prefixes: `recruiter_csv=`, `ats_json=`, `resume=`, `recruiter_notes=`,
`github=`, `linkedin=`.

---

## 4. Mixing structured + unstructured (the core demo)

One structured source + one unstructured source. If they share an email/phone/name
they merge into a single clean profile.

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\structured.csv --input demo\notes.txt --config samples\configs\default.json

 .venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\neeraj.csv --input demo\NeerajKalkoor.pdf --config samples\configs\default.json

 # HR view
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --input samples\ats.json --input samples\resume_jane_doe.docx --input samples\notes.txt --config samples\configs\hr_projection.json

# Technical view
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --input samples\ats.json --input samples\resume_jane_doe.docx --input samples\notes.txt --config samples\configs\technical_projection.json

# 1. Fetch a live profile (accepts a username or a full URL)
.venv\Scripts\python.exe -m candidate_transformer.cli.fetch_github neerajkalkoor --out demo\gh.json
# or:  ...fetch_github https://github.com/neerajkalkoor --out demo\gh.json

# 2. Run it through the pipeline (optionally combined with resume + CSV)
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input "github=demo\gh.json" --config samples\configs\default.json


```

Resume + GitHub + LinkedIn together (shared name merges them):

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\NeerajKalkoor.pdf --input "github=demo\github_payload.json" --input "linkedin=demo\linkedin_payload.json" --config samples\configs\default.json
```

---

## 5. Run a resume on its own

```powershell
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\NeerajKalkoor.pdf --config samples\configs\default.json
```

### Just dump the extracted text from a resume (PDF or DOCX)
```powershell
.venv\Scripts\python.exe -c "from candidate_transformer.adapters import ResumeAdapter, SourceRef; a=ResumeAdapter(); print(a.ingest(SourceRef(location=r'demo\NeerajKalkoor.pdf')).content)"
```

---

## 6. Projection configs (change the output shape, no code change)

```powershell
# Default schema (confidence on, provenance off)
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --config samples\configs\default.json

# Recruiter contact-card view (provenance on, confidence off)
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --config samples\configs\custom.json

# Lean view (provenance + confidence off)
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input samples\recruiter.csv --config samples\configs\custom_minimal.json
```

---

## 7. Skills dictionary (add skills without code changes)

The recognized skills live in `config/skills.json` (canonical name -> aliases).
Edit that file to add new technologies (e.g. `"Next.js": ["nextjs"]`) and re-run —
no rebuild needed.

```powershell
# Validate the JSON is well-formed
.venv\Scripts\python.exe -m json.tool config\skills.json

# Use a custom skills dictionary from a different location (env override)
$env:CANDIDATE_TRANSFORMER_SKILLS = "C:\path\to\my_skills.json"
.venv\Scripts\python.exe -m candidate_transformer.cli.main --input demo\NeerajKalkoor.pdf --config samples\configs\default.json
Remove-Item Env:\CANDIDATE_TRANSFORMER_SKILLS   # unset it afterwards
```

Skill matching is layered: exact (1.0) -> alias (0.8) -> fuzzy/typo (0.6) ->
`unknown_skills` (kept, not dropped).

### Quick check how a single skill string normalizes
```powershell
.venv\Scripts\python.exe -c "from candidate_transformer.normalizers import normalize_skill; print(normalize_skill('Javscript')); print(normalize_skill('postgres')); print(normalize_skill('CoolFramework3000'))"
```

---

## 8. Exit codes

| Code | Meaning |
|:----:|---------|
| `0` | Clean run. |
| `2` | Usage error (missing `--input`) or unparseable `--config`. |
| `1` | Run completed but had source/projection errors (printed to stderr). |

```powershell
# Check the exit code of the last command (PowerShell)
"exit code: $LASTEXITCODE"
```

---

## 9. Tests

```powershell
# Run the whole test suite (373 tests, incl. Hypothesis property tests >=100 cases each)
.venv\Scripts\python.exe -m pytest

# Quiet output
.venv\Scripts\python.exe -m pytest -q

# A single test file
.venv\Scripts\python.exe -m pytest tests\test_cli.py

# A single test by name
.venv\Scripts\python.exe -m pytest tests\test_models.py -k serialization

# Faster local iteration (fewer Hypothesis examples)
$env:HYPOTHESIS_PROFILE = "dev"
.venv\Scripts\python.exe -m pytest
Remove-Item Env:\HYPOTHESIS_PROFILE
```

---

## 10. Useful file locations

| Path | What it is |
|------|------------|
| `candidate_transformer\` | The engine library (engine, adapters, normalizers, models, cli). |
| `config\skills.json` | The editable skills dictionary. |
| `samples\` | Sample input fixtures (csv, json, docx, txt). |
| `samples\configs\` | Example projection configs. |
| `samples\output\` | Reference example output produced by the end-to-end test. |
| `demo\` | Scratch files used in the walkthroughs above. |
| `tests\` | pytest + Hypothesis test suite. |

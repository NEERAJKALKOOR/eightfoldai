# Sample Input Fixtures

These fixtures are realistic, **fictitious** candidate inputs used to exercise the full
transformer pipeline end-to-end. They deliberately describe a small set of *overlapping*
candidates so that identity matching, normalization, merge/conflict resolution, and the
"don't over-merge" edge case are all triggered by real data.

All PII here is invented. `example.com` / `globex.net` addresses and `555-` phone numbers are
non-routable placeholders.

## Files

| File | Source type | Structured? | Purpose |
|------|-------------|-------------|---------|
| `recruiter.csv` | Recruiter CSV | structured | Header `name,email,phone,current_company,title`; includes overlaps and messy values. (Req 1.3) |
| `ats.json` | ATS JSON | structured | Uses **non-canonical** field names (`candidateName`, `emailAddress`, `phoneNumber`, `currentEmployer`, `jobTitle`, `yrsExp`, `skillList`). (Req 1.4) |
| `resume_jane_doe.docx` | Resume | unstructured | Real DOCX (generated via `python-docx`) with contact, experience (with dates), education, and skills sections. (Req 1.7) |
| `resume_jane_doe.txt` | Resume | unstructured | Plain-text mirror of the DOCX, kept for quick inspection / text-only adapter paths. |
| `notes.txt` | Recruiter notes | unstructured | Free-form prose mentioning a candidate, an email + phone, and skills embedded in sentences. (Req 1.8) |
| `configs/` | Projection configs | — | Default and custom `Projection_Config` files (see task 16.2). |

> Note: `resume_jane_doe.docx` is the real DOCX fixture. The `.txt` version exists for
> convenience and human readability; the `ResumeAdapter` (task 5.4) is responsible for
> extracting text from PDF/DOCX, so the DOCX is the primary resume fixture.

## Candidates and overlap design

The fixtures describe **four** distinct people. The table shows which sources mention each one
and what each scenario is meant to test.

| Person | recruiter.csv | ats.json | resume | notes.txt | Scenario exercised |
|--------|:---:|:---:|:---:|:---:|--------------------|
| **Jane Doe** | yes | yes | yes (docx + txt) | yes | The fully-overlapping candidate: appears in **both structured and unstructured** sources (Req 1.2) with an **exact email + phone overlap** that drives identity matching. |
| **John Smith** | yes | — | — | — | Unique person present in **only one** source. Also the "real" half of the near-duplicate-name pair. |
| **Jon Smyth** | — | yes | — | — | **Near-duplicate name** of "John Smith" with **no shared contact** — tests the name-similarity rule and the "don't over-merge" edge case. |
| **Maria Garcia** | — | yes | — | — | Unique person present in **only one** source, with a non-US (GB) phone and unknown/alias skills. |

### Jane Doe — the identity-matching anchor (Req 1.2)

Jane appears across all four files. The overlap is intentionally "messy" so normalization and
merge are exercised:

- `recruiter.csv`: email `Jane.Doe@example.com` (**mixed case**, must lowercase), phone
  `(415) 555-2671` (**non-E.164**, must normalize), title `Senior Engineer`.
- `ats.json`: email `jane.doe@example.com`, phone `+1 (415) 555-2671`, title `Staff Engineer`,
  9 years experience, skills `["py", "JS", "Kubernetes"]`.
- `resume_jane_doe.{docx,txt}`: email `jane.doe@example.com`, phone `+1 415-555-2671`, location
  `San Francisco, CA, USA`, LinkedIn/GitHub links, experience with dates, Berkeley education,
  skills `py, JS, Docker, Kubernetes, PostgreSQL`.
- `notes.txt`: email `jane.doe@example.com`, phone `415.555.2671`, title `Tech Lead`
  (**conflicts** with the CSV and ATS titles), plus skills `py`, `JS`, `Rust` in prose.

This drives:
- **Identity match** on exact normalized email (and phone) across structured + unstructured
  sources (Req 4 rule 1/2).
- **Merge / conflict resolution** on the `headline`/`title` field (Senior Engineer vs Staff
  Engineer vs Tech Lead) resolved by `Winner_Selection_Policy`.
- **List dedup** on phones (three different surface forms of the same number) and skills.

### John Smith vs Jon Smyth — the "don't over-merge" edge case

- `recruiter.csv`: **John Smith**, `john.smith@example.com`, `415-555-9000`, Globex,
  Product Manager.
- `ats.json`: **Jon Smyth**, `jon.smyth@globex.net`, `+1 (415) 555-1234`, Globex,
  Engineering Manager.

The names are visually similar and both work at "Globex", but they share **no email and no
phone**. Their normalized-name similarity (RapidFuzz ratio ≈ 0.84) is **below the 0.9 threshold**,
so the name-similarity rule (Req 4.2 rule 5) is evaluated and correctly leaves them as **separate
identity groups** — i.e. the system does not over-merge two different people who merely have
similar names.

### Unknown and alias skills (for canonical skill mapping)

The fixtures embed both **aliases** and **unknown** skills so the skill normalizer (tasks 3.2,
3.8) is exercised:

- Aliases that should map to a canonical name: `py` / `python3` → Python, `JS` → JavaScript.
- An unknown skill that should normalize to `null`: `wizardry` (Maria Garcia in `ats.json`).

## Messy values intentionally included

- Mixed-case email (`Jane.Doe@example.com`).
- Non-E.164 phones in several surface forms: `(415) 555-2671`, `415-555-9000`, `415.555.2671`,
  `+1 (415) 555-2671`.
- An unparseable phone (`not-a-phone`) and an empty company for **Bob Lee** in `recruiter.csv`,
  to exercise null-on-failure normalization and graceful handling of missing fields.
- A non-US phone (`+44 20 7946 0000`) for Maria Garcia.

> Bob Lee (`recruiter.csv`) is an additional CSV-only row whose phone cannot be parsed and whose
> company is blank — useful for verifying that bad/missing values become `null` rather than
> invented data.

## Mirrored copies

Small copies of these fixtures are mirrored under `tests/fixtures/` so tests can load them
without depending on the repo-root `samples/` path.

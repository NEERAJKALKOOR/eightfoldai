# Technical Design — Multi-Source Candidate Data Transformer

**One-pager · Step 1 (no code).** Turns messy multi-source candidate inputs into one
clean, explainable canonical profile, then projects it into any caller-requested
schema. Guiding principle: **a wrong-but-confident value is worse than an
honestly-empty one** — when a value can't be determined, we record `null` (never
invent), and every value is traceable to its source.

## Pipeline (8 stages)

```
Ingest → Extract → Normalize → Resolve Identity → Merge → Confidence → Project → Validate
```

- **Ingest** — load each source via a uniform `SourceAdapter`; a missing/garbage source becomes a structured `Error_Report` and the run continues.
- **Extract** — each adapter parses its source into a `PerSourceRecord` (one common shape). Absent fields → `null`, never invented. Every value records its extraction `method`.
- **Normalize** — convert raw values to canonical formats (below).
- **Resolve Identity** — group records that refer to the same person; assign a deterministic `candidate_id`.
- **Merge** — combine a group into one canonical record, resolving conflicts by a fixed policy.
- **Confidence** — score every field and the overall record.
- **Project** — reshape the canonical record into the caller's output schema (read-only).
- **Validate** — check the projected output against the requested schema.

The hard boundary is **canonical record vs. projection**: everything before it is *cleaning*; everything after is *presentation*. One canonical record can drive many output schemas without re-parsing or re-merging.

## Canonical schema & normalized formats

Fixed fields: `candidate_id, full_name, emails[], phones[], location{city,region,country}, links{linkedin,github,portfolio,other[]}, headline, years_experience, skills[{name,confidence,sources[]}], experience[{company,title,start,end,summary}], education[{institution,degree,field,end_year}], unknown_skills[], provenance[{field,value,source,method,confidence}], overall_confidence`.

| Field | Canonical format | Quality rule |
|-------|------------------|--------------|
| phone | **E.164** (`+14155552671`) via `phonenumbers` | non-parseable → `null` |
| date | **YYYY-MM** | year-only → assume `-01` (lower quality) |
| country | **ISO-3166 alpha-2** via `pycountry` | unresolvable → `null` |
| skill | **Canonical_Skill_Name** (external `config/skills.json`, exact→alias→fuzzy) | out-of-vocabulary → `unknown_skills`, never dropped silently |
| email | trimmed + lowercased | best-effort, never nulled if non-empty |

## Merge / conflict resolution & confidence

- **Match keys (in order, first hit wins):** exact email → exact phone → email+name → phone+name → name similarity > 0.9 (RapidFuzz). Union-find makes grouping transitive and order-independent.
- **Winner for single-valued fields:** ordered comparator — **SourcePriority** (Recruiter CSV > ATS JSON > Resume > LinkedIn > GitHub > Recruiter notes) → Field_Confidence → Normalization_Quality → stable lexical tie-break. No guessing; deterministic.
- **List-valued fields** (emails, phones, skills, links.other): combined across sources, deduplicated, deterministically sorted.
- **Confidence:** `Field_Confidence = clamp(0.5·SourceReliability + 0.3·Agreement + 0.2·NormalizationQuality, 0, 1)`, where Agreement = (sources supplying the winning value) / (sources containing the field). Null fields score `0.0`. `Overall_Confidence` = mean of non-null field confidences. Identical inputs → identical scores.
- **Provenance:** every value keeps `{field, value, source, method, confidence}`; a field no source supplied gets a `value=null` "not found" entry — so any value (or absence) is auditable.

## Runtime custom output (projection + validation)

A JSON config reshapes the output at runtime — **same engine, no code changes**. Per field it can: select a subset, rename/remap from a canonical path (`from`/`path`, supporting nested `location.city`, indexed `phones[0]`, array projection `skills[].name`), apply per-field normalization (`E164`, `canonical`, `lowercase`/`uppercase`), declare type/required/enum, and set missing-value behavior (`null` / `omit` / `error`, per-field or global). Provenance and confidence are toggleable. The projection is **read-only** (never mutates the canonical record), and the **Validation** stage then checks the projected output against the declared schema (type, required, enum, array element type, object subfields), reporting structured errors that name the offending field.

## Edge cases & deliberate scope cuts

**Handled**
1. **Missing / empty / garbage source** — captured as an `Error_Report` tagged with the failing stage; the run never crashes and still emits a profile (all-null if every source fails).
2. **Conflicting values across sources** — resolved deterministically by the Winner_Selection_Policy; the chosen value keeps its provenance + confidence.
3. **Near-duplicate but distinct people** (e.g. *John Smith* vs *Jon Smyth*, no shared email/phone) — kept as separate identity groups to avoid over-merging.
4. **Out-of-vocabulary / unknown skills** — surfaced in `unknown_skills` with provenance instead of being dropped; expanding `config/skills.json` later promotes them with no code change.
5. **Messy PDF text** (collapsed whitespace, section bleed) — education stops at the next section and requires an institution/degree signal; location rejects sentence-like prose — preferring honest `null` over a wrong value.

**Deliberately left out (time pressure)**
- ML-based extraction/OCR (uses deterministic regex/heuristics instead).
- A large learned skills ontology and skill *inference* (uses a curated dictionary + fuzzy match).
- Probabilistic/ML entity resolution and blocking for hundreds of millions of profiles (uses 5 deterministic rules + union-find, scoped to thousands).
- Live GitHub/LinkedIn fetching (adapters read local JSON payloads to stay deterministic and offline) and company/title normalization to external taxonomies.

> Note: this markdown is the design content. Export it to a one-page PDF named
> `<YourFullName>_<YourEmail>_Eightfold.pdf` for submission.

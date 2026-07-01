# Example Projection Configs

These JSON files are example `Projection_Config` documents for the Multi-Source Candidate Data
Transformer. They are consumed at runtime by the projection engine (`--config` on the CLI) to reshape
a `Canonical_Record` into a caller-specified `Projected_Profile` without any code changes.

Each config follows the field-entry shape defined in the design document. Every entry in `fields`
supports:

| Key            | Required | Meaning |
|----------------|----------|---------|
| `name`         | yes      | output field name in the projected profile |
| `from`         | yes      | canonical path to read the value from (rename/remap) |
| `type`         | yes      | declared output type (`string`, `number`, `array`, `object`) |
| `required`     | no       | whether the field must be present in the output |
| `enum`         | no       | allowed set of values |
| `normalize`    | no       | per-field transform (e.g. `lowercase`, `uppercase`) |
| `element_type` | no       | element shape for `array` / subfield shape for `object` |
| `on_missing`   | no       | behavior when the canonical field is absent: `null`, `omit`, or `error` |

Top-level toggles:

- `include_provenance` — when `false`, provenance is omitted from the output (Req 8.11).
- `include_confidence` — when `false`, field and overall confidence are omitted (Req 8.12).

## `default.json` — default schema (matches the design example)

The default-schema projection, identical in shape to the "Example `Projection_Config`" in the design
document. Provenance is **off** and confidence is **on**. It demonstrates the full range of runtime
config capabilities (Req 8.1):

- **Field selection** — only the listed canonical fields appear in the output (Req 8.3).
- **Rename / remap** — `candidate_id` is projected as `id`, and `full_name` as `name`, via `from`
  (Req 8.4, 8.8).
- **Nested path** — `location.country` reads the nested country code (Req 8.5).
- **Indexed array path** — `emails[0]` projects the primary email as `primary_email` (Req 8.6).
- **Array projection** — `skills[].name` produces a flat list of skill names (Req 8.7).
- **Object element type** — `experience` is projected as an array with a declared element shape.
- **Per-field normalization** — `linkedin` (from `links.linkedin`) is lowercased (Req 8.10).
- **enum constraint** — `country` is constrained to `["US", "IN", "GB"]` (Req 9.5).
- **Mixed `on_missing`** — `id` uses `error` (required, Req 8.15), `name`/`primary_email`/`skills`
  use `null` (Req 8.13), and `country`/`linkedin` use `omit` (Req 8.14).

## `custom.json` — recruiter contact-card view

A custom projection that selects a **different subset** of fields with **different renames**, turns
provenance **on** and confidence **off**, and exercises every `on_missing` behavior:

- **Different subset + renames** — `candidate` (from `full_name`), `work_email` (from `emails[0]`),
  `contact_phone` (from `phones[0]`), `city` (from `location.city`), `headline`, and `github_url`
  (from `links.github`) (Req 8.3, 8.4, 8.8).
- **`on_missing: error`** — on the required `candidate` field (Req 8.15).
- **`on_missing: null`** — on `work_email`, `city`, and `headline` (Req 8.13).
- **`on_missing: omit`** — on `contact_phone` and `github_url` (Req 8.14).
- **Nested + indexed paths** — `location.city`, `emails[0]`, `phones[0]` (Req 8.5, 8.6).
- **Per-field normalization** — `github_url` is lowercased (Req 8.10).

## `custom_minimal.json` — lean custom view

A smaller, leaner projection that selects a **different, reduced subset** of fields, uses
**different renames**, and turns provenance and confidence **both off** for a compact payload. It
exercises a mix of every `on_missing` behavior:

- **Smaller subset + different renames** — `id`, `name` (from `full_name`), `email` (from
  `emails[0]`), `phone` (from `phones[0]`), `country` (from `location.country`), and `top_skills`
  (from `skills[].name`) (Req 8.3, 8.4, 8.8).
- **Provenance + confidence off** — `include_provenance: false`, `include_confidence: false` for a
  lean output (Req 8.11, 8.12).
- **`on_missing: error`** — on the required `id` field, reports a projection error when absent
  (Req 8.15).
- **`on_missing: null`** — on `name`, `email`, and `top_skills`, emits null when absent (Req 8.13).
- **`on_missing: omit`** — on `phone` and `country`, drops the field entirely when absent
  (Req 8.14).
- **Nested, indexed, and array-projection paths** — `location.country`, `emails[0]`/`phones[0]`,
  and `skills[].name` (Req 8.5, 8.6, 8.7).

## Validating

These files are plain JSON and can be validated with:

```
python -m json.tool samples/configs/default.json
python -m json.tool samples/configs/custom.json
python -m json.tool samples/configs/custom_minimal.json
```

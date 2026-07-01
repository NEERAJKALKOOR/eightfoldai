# Requirements Document

## Introduction

The Multi-Source Candidate Data Transformer is a deterministic, explainable pipeline that ingests candidate information from multiple heterogeneous sources, extracts and normalizes the data, deduplicates records that refer to the same person, resolves conflicts between sources, and produces a single canonical candidate profile. Each value in the profile is traceable to its originating source and extraction method (provenance) and carries a confidence score. A runtime-configurable projection layer reshapes the canonical record into a caller-specified output schema without code changes.

The guiding principle is that a wrong-but-confident value is worse than an honestly-empty one: when a value cannot be determined, the system records a null rather than inventing a value. The system must run without crashing when sources are missing, empty, or malformed, must produce identical output for identical inputs, and must scale to thousands of candidates.

## Glossary

- **Transformer**: The complete pipeline system that converts raw multi-source inputs into projected candidate profiles.
- **Source**: A single input artifact of a known type (Recruiter CSV, ATS JSON, GitHub URL, LinkedIn URL, Resume file, Recruiter notes file).
- **Structured_Source**: A source with a defined schema or machine-readable format (Recruiter CSV, ATS JSON).
- **Unstructured_Source**: A source containing free-form or semi-structured prose (GitHub profile, LinkedIn profile, Resume, Recruiter notes).
- **Ingestion_Module**: The Transformer component that reads and loads raw source content.
- **Extraction_Module**: The Transformer component that pulls candidate fields out of a single source.
- **Normalization_Module**: The Transformer component that converts extracted values into canonical formats.
- **Identity_Resolver**: The Transformer component that determines which extracted records refer to the same person.
- **Merge_Module**: The Transformer component that combines records of one person into a single canonical record and resolves conflicts.
- **Projection_Module**: The Transformer component that reshapes a Canonical_Record into a caller-specified output using the Projection_Config.
- **Validation_Module**: The Transformer component that checks a projected output against the requested schema.
- **Canonical_Record**: The internal, fixed-schema representation of one candidate before projection.
- **Canonical_Schema**: The fixed set of fields and formats defining a Canonical_Record.
- **Projected_Profile**: The output produced by the Projection_Module for one candidate.
- **Projection_Config**: The runtime configuration that selects, renames, normalizes, and shapes the projected output.
- **Provenance**: A record for each canonical value with the shape `{ field, value, source, method, confidence }`, where `field` is the canonical field name, `value` is the recorded value, `source` identifies the originating Source, `method` is the extraction method used to derive the value, and `confidence` is the Field_Confidence assigned to the value.
- **Confidence**: A numeric score in the range 0.0 to 1.0 expressing the system's certainty in a value.
- **Field_Confidence**: The Confidence score attached to a single field or value.
- **Overall_Confidence**: The Confidence score for an entire Canonical_Record.
- **Winner_Selection_Policy**: The deterministic rule set used to choose a value when sources conflict.
- **SourcePriority**: The fixed, ordered ranking of source types from most to least authoritative, used to make Winner_Selection_Policy deterministic. The ranking from highest to lowest is: Recruiter CSV, ATS JSON, Resume, LinkedIn, GitHub, Recruiter notes.
- **Source_Reliability**: A fixed numeric weight in the range 0.0 to 1.0 assigned to each source type, used as an input to confidence scoring and winner selection. The weights are: Recruiter CSV 0.95, ATS JSON 0.90, Resume 0.85, LinkedIn 0.80, GitHub 0.70, Recruiter notes 0.60.
- **Agreement_Score**: A numeric value in the range 0.0 to 1.0 expressing how strongly the available sources agree on a value, computed as `Agreement_Score = (number of sources that supply the selected/winning value) / (number of sources that contain the field)`. WHERE no source contains the field, the Agreement_Score is 0.0 to avoid division by zero.
- **Normalization_Quality**: A numeric value in the range 0.0 to 1.0 expressing how cleanly a raw value converted to its canonical format, where a fully successful conversion scores higher than a partial or fallback conversion.
- **Confidence_Formula**: The deterministic formula used to compute Field_Confidence: `confidence = 0.5 * Source_Reliability + 0.3 * Agreement_Score + 0.2 * Normalization_Quality`, with the result clamped to the range [0.0, 1.0].
- **Controlled_Skill_Vocabulary**: The fixed set of Canonical_Skill_Names together with their accepted aliases.
- **Canonical_Skill_Name**: A standardized skill label drawn from the Controlled_Skill_Vocabulary.
- **Skill_Alias**: An accepted alternative spelling or label that maps to a Canonical_Skill_Name (for example, "py", "python3", and "Python Programming" all map to "Python"; "JS", "Javascript", and "Java Script" all map to "JavaScript").
- **Identity_Match_Priority**: The fixed, ordered set of rules the Identity_Resolver applies to decide whether two records refer to the same candidate. Full-name similarity is computed as a normalized Levenshtein similarity ratio (the RapidFuzz ratio) producing a value in the range 0.0 to 1.0, so the greater-than-0.9 threshold is reproducible.
- **Error_Report**: A structured error object with the shape `{ source, stage, error }`, where `source` identifies the originating Source, `stage` identifies the pipeline stage that failed, and `error` is a human-readable description.
- **Logging_Level**: The severity classification of a log entry, one of INFO, WARNING, or ERROR.
- **Log_Entry**: A structured log record with the shape `{ timestamp, level, module, message }`, where `timestamp` is the time the entry was emitted, `level` is the Logging_Level, `module` identifies the Transformer component that emitted the entry, and `message` is a human-readable description.
- **CLI**: The command-line interface surface that accepts input files and a config and emits JSON.
- **E.164**: The international telephone number format (for example, +14155552671).
- **ISO-3166-alpha-2**: The two-letter country code standard (for example, US, IN, GB).

## Requirements

### Requirement 1: Multi-Source Ingestion

**User Story:** As a data engineer, I want the Transformer to ingest candidate data from multiple source types in one run, so that I can build a profile from all available information about a candidate.

#### Acceptance Criteria

1. WHEN a run is started with one or more source references, THE Ingestion_Module SHALL load each referenced source into raw content for extraction.
2. THE Ingestion_Module SHALL support at least one Structured_Source type and at least one Unstructured_Source type in a single run.
3. THE Ingestion_Module SHALL support the Recruiter CSV source type containing name, email, phone, current_company, and title fields.
4. THE Ingestion_Module SHALL support the ATS JSON source type whose field names differ from the Canonical_Schema field names.
5. THE Ingestion_Module SHALL support the GitHub profile source type referenced by a public profile URL.
6. THE Ingestion_Module SHALL support the LinkedIn profile source type referenced by a public profile URL.
7. THE Ingestion_Module SHALL support the Resume source type provided as a PDF or DOCX file.
8. THE Ingestion_Module SHALL support the Recruiter notes source type provided as a plain-text file.
9. WHEN a source is loaded, THE Ingestion_Module SHALL record the source type and source identifier for use in provenance.

### Requirement 2: Per-Source Field Extraction

**User Story:** As a data engineer, I want each source parsed into candidate fields independently, so that the contribution of every source is isolated and traceable.

#### Acceptance Criteria

1. WHEN a source is ingested, THE Extraction_Module SHALL extract candidate fields from that source into an intermediate per-source record.
2. THE Extraction_Module SHALL map each extracted value to its corresponding Canonical_Schema field.
3. WHERE a source contains field names that do not match the Canonical_Schema, THE Extraction_Module SHALL map those field names to Canonical_Schema fields using a defined mapping.
4. IF a Canonical_Schema field is absent from a source, THEN THE Extraction_Module SHALL set that field to null in the per-source record.
5. WHEN a value is extracted, THE Extraction_Module SHALL record the extraction method used for that value for use in provenance.
6. IF the Extraction_Module cannot determine a value for a field, THEN THE Extraction_Module SHALL set that field to null and SHALL NOT substitute an inferred or invented value.

### Requirement 3: Value Normalization

**User Story:** As a downstream consumer, I want extracted values converted into consistent canonical formats, so that profiles are comparable and machine-usable.

#### Acceptance Criteria

1. WHEN a phone number is normalized, THE Normalization_Module SHALL convert it to E.164 format.
2. IF a phone number cannot be converted to E.164 format, THEN THE Normalization_Module SHALL set the phone value to null.
3. WHEN a date is normalized, THE Normalization_Module SHALL convert it to YYYY-MM format.
4. IF a date cannot be converted to YYYY-MM format, THEN THE Normalization_Module SHALL set the date value to null.
5. WHEN a country value is normalized, THE Normalization_Module SHALL convert it to an ISO-3166-alpha-2 code.
6. IF a country value cannot be converted to an ISO-3166-alpha-2 code, THEN THE Normalization_Module SHALL set the country value to null.
7. WHEN a skill is normalized, THE Normalization_Module SHALL map the skill to a Canonical_Skill_Name by resolving any matching Skill_Alias in the Controlled_Skill_Vocabulary to its canonical name.
8. IF a skill cannot be mapped to a Canonical_Skill_Name in the Controlled_Skill_Vocabulary, THEN THE Normalization_Module SHALL set the skill name to null.
9. WHEN an email is normalized, THE Normalization_Module SHALL convert the email to lowercase and remove surrounding whitespace.
10. WHEN a value is normalized, THE Normalization_Module SHALL record a Normalization_Quality score in the range 0.0 to 1.0 for that value for use in confidence scoring.

### Requirement 4: Identity Matching and Deduplication

**User Story:** As a data engineer, I want records that refer to the same person grouped together, so that one candidate yields exactly one profile.

#### Acceptance Criteria

1. WHEN multiple per-source records are available, THE Identity_Resolver SHALL group records that refer to the same candidate into one identity group.
2. THE Identity_Resolver SHALL determine identity matches by applying the Identity_Match_Priority rules in the following fixed order: (1) exact normalized email match, (2) exact normalized phone match, (3) exact normalized email combined with full-name match, (4) exact normalized phone combined with full-name match, (5) full-name similarity greater than 0.9 computed as a normalized Levenshtein similarity ratio (the RapidFuzz ratio), provided no strong contact identifier contradicts the match (see 2a).
2a. WHEN evaluating rule (5), THE Identity_Resolver SHALL reject a name-similarity match if both records carry an email identifier and those emails do not overlap, OR both records carry a phone identifier and those phones do not overlap. Rationale: a shared name is the weakest identity signal, and differing strong contact identifiers (email/phone) are positive evidence that two records describe different people. (This refines the base spec's rule (5) to avoid false merges of distinct people who share a name; the same person is still merged across sources that lack a shared contact channel, i.e. where no identifier contradicts the match.)
3. WHERE two records satisfy any rule in the Identity_Match_Priority order, THE Identity_Resolver SHALL assign both records to the same identity group.
4. IF two records satisfy none of the Identity_Match_Priority rules, THEN THE Identity_Resolver SHALL assign the records to separate identity groups.
5. WHEN evaluating the Identity_Match_Priority rules, THE Identity_Resolver SHALL apply the rules in order and SHALL assign records to the same identity group on the first rule that is satisfied.
6. WHEN identity groups are formed, THE Identity_Resolver SHALL assign one candidate_id to each identity group.
7. WHEN a candidate_id is assigned, THE Identity_Resolver SHALL derive the candidate_id deterministically from the normalized identity key of the group using a fixed method such as UUID5(namespace, normalized_email) or SHA256(normalized_email).
8. WHEN the same source content is processed again, THE Identity_Resolver SHALL produce the same candidate_id for the same identity group.

### Requirement 5: Merge and Conflict Resolution

**User Story:** As a downstream consumer, I want conflicting values from different sources resolved by a defined policy, so that each canonical field holds one trustworthy value.

#### Acceptance Criteria

1. WHEN an identity group contains records from multiple sources, THE Merge_Module SHALL combine those records into one Canonical_Record.
2. WHERE multiple sources provide different values for a single-valued field, THE Merge_Module SHALL select one value using the Winner_Selection_Policy.
3. THE Winner_Selection_Policy SHALL deterministically select a winning value based on SourcePriority, then Field_Confidence, then Normalization_Quality, then stable lexical ordering of the candidate values as the final tie-breaker.
4. WHERE candidate values originate from different source types, THE Winner_Selection_Policy SHALL prefer the value from the source with the higher SourcePriority ranking.
5. WHERE the Winner_Selection_Policy produces a tie after applying SourcePriority, Field_Confidence, and Normalization_Quality, THE Merge_Module SHALL break the tie using a final deterministic rule of stable lexical ordering of the candidate values.
6. WHERE multiple sources provide values for a list-valued field including emails, phones, and skills, THE Merge_Module SHALL combine the values into a deduplicated list.
7. WHEN a winning value is selected, THE Merge_Module SHALL retain the provenance and Field_Confidence of the selected value.

### Requirement 6: Provenance Tracking

**User Story:** As a reviewer, I want every value traceable to where it came from and how it was derived, so that I can audit and trust the profile.

#### Acceptance Criteria

1. WHEN a value is placed into a Canonical_Record, THE Merge_Module SHALL record a provenance entry with the shape `{ field, value, source, method, confidence }` for that value, where `field` is the canonical field name, `value` is the recorded value, `source` identifies the originating Source, `method` is the extraction method, and `confidence` is the Field_Confidence.
2. THE Transformer SHALL include the provenance entries in the Canonical_Record.
3. WHERE a field value is null because no source provided it, THE Transformer SHALL record a provenance entry with `value` set to null and `source` and `method` indicating the value was not found.
4. WHEN a list-valued field combines values from multiple sources, THE Merge_Module SHALL record a provenance entry for each contributing value.

### Requirement 7: Confidence Scoring

**User Story:** As a downstream consumer, I want a confidence score on each value and on the profile as a whole, so that I can decide how much to trust the data.

#### Acceptance Criteria

1. WHEN a value is placed into a Canonical_Record, THE Merge_Module SHALL assign a Field_Confidence in the range 0.0 to 1.0 to that value.
2. WHEN computing Field_Confidence, THE Merge_Module SHALL apply the Confidence_Formula `confidence = 0.5 * Source_Reliability + 0.3 * Agreement_Score + 0.2 * Normalization_Quality` and SHALL clamp the result to the range [0.0, 1.0].
3. THE Merge_Module SHALL determine Source_Reliability using the fixed weights: Recruiter CSV 0.95, ATS JSON 0.90, Resume 0.85, LinkedIn 0.80, GitHub 0.70, Recruiter notes 0.60.
4. WHEN computing Agreement_Score for a field, THE Merge_Module SHALL compute Agreement_Score as the number of sources that supply the selected winning value divided by the number of sources that contain the field, and WHERE no source contains the field THE Merge_Module SHALL set Agreement_Score to 0.0.
5. WHERE multiple sources agree on a value, THE Merge_Module SHALL assign a Field_Confidence greater than or equal to the Field_Confidence assigned when only one source provides the value.
6. WHEN a Canonical_Record is assembled, THE Merge_Module SHALL compute an Overall_Confidence in the range 0.0 to 1.0 for the record.
7. WHERE a field value is null, THE Merge_Module SHALL assign that field a Field_Confidence of 0.0.
8. WHEN the same inputs are processed again, THE Merge_Module SHALL compute the same Field_Confidence and Overall_Confidence values.

### Requirement 8: Configurable Projection Layer

**User Story:** As an integrator, I want to reshape the output at runtime via configuration, so that different consumers receive the schema they need without code changes.

#### Acceptance Criteria

1. WHEN a run is started with a Projection_Config, THE Projection_Module SHALL produce a Projected_Profile shaped according to that Projection_Config.
2. THE Projection_Module SHALL read every output value from the Canonical_Record without modifying the Canonical_Record.
3. WHERE the Projection_Config selects a subset of fields, THE Projection_Module SHALL include only the selected fields in the Projected_Profile.
4. WHERE the Projection_Config specifies a source canonical path for a field, THE Projection_Module SHALL read the value from that canonical path and place it at the configured output field name.
5. WHERE the Projection_Config specifies a nested canonical path, THE Projection_Module SHALL resolve the nested path into the Canonical_Record structure and read the value at that location.
6. WHERE the Projection_Config specifies an indexed array element path such as `phones[0]`, THE Projection_Module SHALL read the element at the given index of that list-valued field.
7. WHERE the Projection_Config specifies an array projection path such as `skills[].name`, THE Projection_Module SHALL produce a list containing the named subfield from each element of that list-valued field.
8. WHERE the Projection_Config renames a field to a different output field name, THE Projection_Module SHALL place the value under the configured output field name.
9. WHERE the Projection_Config marks a field as optional, THE Projection_Module SHALL apply the configured missing-value behavior for that field when the field is absent from the Canonical_Record.
10. WHERE the Projection_Config specifies a per-field normalization type, THE Projection_Module SHALL apply that normalization to the field value.
11. WHERE the Projection_Config sets provenance inclusion to off, THE Projection_Module SHALL omit provenance from the Projected_Profile.
12. WHERE the Projection_Config sets confidence inclusion to off, THE Projection_Module SHALL omit Field_Confidence and Overall_Confidence from the Projected_Profile.
13. WHERE the Projection_Config sets the missing-value behavior to null for a field, THE Projection_Module SHALL emit a null value when that field is absent from the Canonical_Record.
14. WHERE the Projection_Config sets the missing-value behavior to omit for a field, THE Projection_Module SHALL exclude that field from the Projected_Profile when the field is absent from the Canonical_Record.
15. IF the Projection_Config sets the missing-value behavior to error for a required field and that field is absent from the Canonical_Record, THEN THE Projection_Module SHALL report a projection error identifying the field.
16. IF the Projection_Config specifies an invalid canonical path, including an out-of-range array index such as `phones[50]` or a non-existent subfield such as `skills[].abc`, THEN THE Projection_Module SHALL report a projection error identifying the invalid path.

### Requirement 9: Output Schema Validation

**User Story:** As an integrator, I want the projected output validated against the requested schema, so that I can rely on the output structure.

#### Acceptance Criteria

1. WHEN a Projected_Profile is produced, THE Validation_Module SHALL validate the Projected_Profile against the schema defined by the Projection_Config.
2. IF a Projected_Profile fails validation against the requested schema, THEN THE Validation_Module SHALL report a validation error identifying the failing field and the reason.
3. WHERE a field declares a type in the Projection_Config, THE Validation_Module SHALL confirm the projected value matches the declared type.
4. WHERE a field is declared required in the Projection_Config, THE Validation_Module SHALL confirm the field is present in the Projected_Profile.
5. WHERE a field declares an enumerated set of allowed values in the Projection_Config, THE Validation_Module SHALL confirm the projected value is a member of that set.
6. WHERE a field declares an array type in the Projection_Config, THE Validation_Module SHALL confirm the projected value is a list and SHALL confirm each element matches the declared element type.
7. WHERE a field declares an object structure in the Projection_Config, THE Validation_Module SHALL confirm the projected value contains the declared subfields with their declared types.

### Requirement 10: Robustness and Graceful Degradation

**User Story:** As an operator, I want the pipeline to keep running when a source is missing or corrupt, so that one bad input does not block the whole run.

#### Acceptance Criteria

1. IF a referenced source is missing, THEN THE Ingestion_Module SHALL record an Error_Report with the shape `{ source, stage, error }` and SHALL continue the run with the remaining sources.
2. IF a source is empty, THEN THE Extraction_Module SHALL produce a per-source record with all fields set to null and SHALL continue the run.
3. IF a source is malformed and cannot be parsed, THEN THE Extraction_Module SHALL record an Error_Report with the shape `{ source, stage, error }` and SHALL continue the run with the remaining sources.
4. IF every source for a run fails to load or parse, THEN THE Transformer SHALL produce a Canonical_Record with all derivable fields set to null and SHALL report the Error_Report entries for each failed source.
5. WHEN a source error occurs, THE Transformer SHALL complete the run without terminating abnormally.

### Requirement 11: Structured Error Reporting and Logging

**User Story:** As an operator, I want errors reported in a consistent structure and logs at distinct severity levels, so that I can diagnose and triage problems.

#### Acceptance Criteria

1. WHEN the Transformer reports an error, THE Transformer SHALL produce an Error_Report with the shape `{ source, stage, error }`.
2. THE Transformer SHALL set the `stage` field of each Error_Report to the pipeline stage in which the error occurred.
3. WHEN the Transformer emits a log entry, THE Transformer SHALL produce a Log_Entry with the shape `{ timestamp, level, module, message }`.
4. THE Transformer SHALL emit log entries at the INFO Logging_Level for normal progress events.
5. THE Transformer SHALL emit log entries at the WARNING Logging_Level for recoverable conditions including missing, empty, or skipped values.
6. WHEN an Error_Report is produced, THE Transformer SHALL emit a corresponding log entry at the ERROR Logging_Level.

### Requirement 12: Determinism

**User Story:** As an auditor, I want identical inputs to produce identical outputs, so that results are reproducible and explainable.

#### Acceptance Criteria

1. WHEN the same set of source contents and the same Projection_Config are processed in separate runs, THE Transformer SHALL produce identical Projected_Profile output.
2. THE Transformer SHALL produce list-valued fields in a deterministic order across runs.
3. THE Transformer SHALL produce provenance entries in a deterministic order across runs.

### Requirement 13: Command-Line Interface

**User Story:** As an operator, I want to run the Transformer from the command line pointing at input files and a config, so that I can integrate it into scripts and pipelines.

#### Acceptance Criteria

1. WHEN the CLI is invoked with one or more input file references and a Projection_Config reference, THE Transformer SHALL run the pipeline and emit the Projected_Profile output as JSON.
2. WHERE the CLI is invoked with an output file path, THE CLI SHALL write the JSON output to that path.
3. WHERE the CLI is invoked without an output file path, THE CLI SHALL print the JSON output to standard output.
4. IF the CLI is invoked without a required input reference, THEN THE CLI SHALL report a usage error identifying the missing argument.
5. IF the CLI is invoked with a Projection_Config that cannot be parsed, THEN THE CLI SHALL report a configuration error identifying the problem.
6. WHEN the run completes successfully, THE CLI SHALL exit with a success status code.
7. IF the run completes with one or more source or projection errors, THEN THE CLI SHALL exit with a non-zero status code and SHALL report the Error_Report entries.

### Requirement 14: Scale and Batch Isolation

**User Story:** As an operator, I want the pipeline to handle large batches where each candidate is processed independently, so that I can process realistic candidate volumes without one failure affecting others.

#### Acceptance Criteria

1. WHEN a run includes source data for thousands of candidates, THE Transformer SHALL process all candidates and produce one Projected_Profile per identity group.
2. THE Transformer SHALL complete a batch run for thousands of candidates without exhausting available memory.
3. THE Transformer SHALL process each candidate independently of every other candidate in the batch.
4. IF processing of one candidate fails, THEN THE Transformer SHALL record an Error_Report for that candidate and SHALL continue processing the remaining candidates.

### Requirement 15: Canonical Record Isolation

**User Story:** As an integrator, I want projection to be a read-only transformation that never alters the canonical data, so that the same canonical record can drive multiple projections safely.

#### Acceptance Criteria

1. WHEN the Projection_Module produces a Projected_Profile, THE Projection_Module SHALL NOT modify the Canonical_Record.
2. WHERE multiple Projection_Configs are applied to the same Canonical_Record, THE Projection_Module SHALL produce a Projected_Profile for each Projection_Config from the unchanged Canonical_Record.
3. THE Projection_Module SHALL perform projection as a read-only transformation of the Canonical_Record.

## Non-Functional Requirements

### Requirement 16: Determinism (Non-Functional)

**User Story:** As an auditor, I want the system to be deterministic, so that the same inputs and configuration always yield the same result.

#### Acceptance Criteria

1. THE Transformer SHALL produce deterministic output for identical inputs and configuration.

### Requirement 17: Explainability (Non-Functional)

**User Story:** As a reviewer, I want every output value explained, so that I can audit how each value was derived.

#### Acceptance Criteria

1. THE Transformer SHALL associate every Canonical_Record field with its source, extraction method, and Field_Confidence.
2. WHERE a field value is null, THE Transformer SHALL record an explanation indicating the value was not found.

### Requirement 18: Robustness (Non-Functional)

**User Story:** As an operator, I want the system to never crash on malformed input, so that batch runs always complete.

#### Acceptance Criteria

1. IF any source is missing, empty, or malformed, THEN THE Transformer SHALL complete the run without terminating abnormally.

### Requirement 19: Performance (Non-Functional)

**User Story:** As an operator, I want the system to scale to large batches, so that realistic candidate volumes are processed efficiently.

#### Acceptance Criteria

1. THE Transformer SHALL process batches efficiently without exhausting memory.

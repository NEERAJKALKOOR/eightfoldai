"""The TransformerEngine orchestration (task 14.1, Req 10, 11, 12, 14).

This module wires the eight pipeline stages described in the design into a single
``TransformerEngine.run(refs, config)`` entry point that turns a set of source
references into a :class:`~candidate_transformer.models.run_result.RunResult` --
one ``Projected_Profile`` per identity group, plus every structured
``Error_Report`` and an ``exit_code``.

Pipeline (design "Pipeline Stages")::

    Ingest -> Extract -> Normalize -> Resolve Identity -> Merge -> Score
           -> Project -> Validate

The orchestration owns the *cross-cutting* guarantees the individual stages cannot
provide on their own:

* **Robustness / never-crash** (Req 10.5, 18.1): every risky step runs inside a
  boundary that converts a failure into a structured ``Error_Report`` and lets the
  run continue. The top-level :meth:`TransformerEngine.run` additionally wraps the
  whole pipeline so *no* exception can escape -- it always returns a ``RunResult``.
* **Graceful degradation** (Req 10.1-10.4): a missing/unresolvable source becomes an
  ``ingest`` error and the run continues with the rest; an empty source yields an
  all-null per-source record; a malformed source becomes an ``extract`` error; and
  when *every* source fails the engine still emits an all-null canonical record.
* **Per-candidate isolation** (Req 14.1, 14.3, 14.4): identity grouping is the only
  cross-candidate stage; thereafter each group is merged, scored, projected, and
  validated inside its own boundary, so one candidate's failure produces an error for
  that candidate alone and never affects the others.
* **Structured logging** (Req 11.3-11.6): INFO for progress, WARNING for recoverable
  conditions (missing/empty/skipped), and an ERROR alongside every ``Error_Report``.
  Log timestamps are operational only and never enter the projected output, so
  determinism of the profiles is preserved (Req 12.1).

Determinism (Req 12.1): identity groups are processed in the deterministic order the
:class:`~candidate_transformer.engine.identity.IdentityResolver` emits them, list
fields are deduplicated and sorted by the merge module, and no wall-clock or
randomness feeds the output.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from candidate_transformer.adapters import (
    AdapterRegistry,
    IngestError,
    NoAdapterFoundError,
    SourceRef,
    build_default_registry,
    reliability_of,
)
from candidate_transformer.models import (
    CanonicalRecord,
    ErrorReport,
    ExperienceEntry,
    EducationEntry,
    LogEntry,
    Links,
    Location,
    PerSourceRecord,
    ProvenanceEntry,
    RunResult,
    Skill,
    new_null_canonical_record,
    new_null_per_source_record,
)
from candidate_transformer.normalizers import (
    normalize_country,
    normalize_date,
    normalize_email,
    normalize_phone,
    normalize_skill,
)

from .confidence import agreement_score, field_confidence, overall_confidence
from .identity import assign_candidate_ids, group
from .merge import (
    CandidateValue,
    ListContribution,
    combine_list_field,
    make_provenance,
    not_found_provenance,
    order_provenance,
    select_winner,
)
from .projection import ProjectionConfig, project
from .validation import validate

__all__ = ["TransformerEngine", "run"]

# Type alias: a per-field normalizer turning a raw value into (value|None, quality).
_Normalizer = Callable[[Any], "tuple[Any, float]"]


# --------------------------------------------------------------------------- #
# Structured logging (Req 11.3-11.6)
# --------------------------------------------------------------------------- #
class _RunLogger:
    """Collects :class:`LogEntry` records and mirrors them to the stdlib logger.

    Each entry carries an operational ``timestamp`` (never part of the projected
    output, preserving determinism, Req 12.1). The accumulated :attr:`entries` are
    exposed on the engine after a run for inspection/testing (Req 11.4-11.6).
    """

    def __init__(self) -> None:
        self.entries: list[LogEntry] = []
        self._py = logging.getLogger("candidate_transformer.engine")

    def _emit(self, level: str, module: str, message: str) -> None:
        self.entries.append(
            LogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                level=level,
                module=module,
                message=message,
            )
        )
        self._py.log(getattr(logging, level, logging.INFO), "%s: %s", module, message)

    def info(self, module: str, message: str) -> None:
        """Emit an INFO entry for a normal progress event (Req 11.4)."""
        self._emit("INFO", module, message)

    def warning(self, module: str, message: str) -> None:
        """Emit a WARNING entry for a recoverable condition (Req 11.5)."""
        self._emit("WARNING", module, message)

    def error(self, module: str, message: str) -> None:
        """Emit an ERROR entry (paired with an Error_Report) (Req 11.6)."""
        self._emit("ERROR", module, message)


# --------------------------------------------------------------------------- #
# Small value helpers
# --------------------------------------------------------------------------- #
def _iter_items(value: Any) -> list[Any]:
    """Flatten a scalar-or-list field value into a list of non-null items."""
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return [value]


def _passthrough(raw: Any) -> tuple[Any, float]:
    """Faithful-copy normalizer: present values keep quality ``1.0`` (never invents)."""
    if raw is None:
        return (None, 0.0)
    return (raw, 1.0)


def _normalize_location(raw: Any) -> tuple[Location | None, float]:
    """Normalize a :class:`Location` (its country -> ISO-3166 alpha-2) (Req 3.5, 3.6)."""
    if not isinstance(raw, Location):
        return (None, 0.0)
    country: str | None = None
    country_quality = 1.0
    if raw.country:
        country, country_quality = normalize_country(raw.country)
        if country is None:
            # Unresolvable country -> null country, but keep city/region (never invent).
            country_quality = 0.5
    merged = Location(city=raw.city, region=raw.region, country=country)
    if merged.city is None and merged.region is None and merged.country is None:
        return (None, 0.0)
    quality = country_quality if raw.country else 1.0
    return (merged, quality)


# --------------------------------------------------------------------------- #
# Merge primitives (single-valued + list-valued), wired with confidence
# --------------------------------------------------------------------------- #
def _merge_single(
    field_name: str,
    records: Iterable[PerSourceRecord],
    normalizer: _Normalizer,
) -> tuple[Any, list[ProvenanceEntry], float]:
    """Resolve one single-valued field across the group via Winner_Selection_Policy.

    Gathers each source's normalized value, scores every candidate with the
    Confidence_Formula (using its own Agreement_Score), selects the winner with the
    :func:`select_winner` comparator, and returns ``(value, [provenance], confidence)``.
    A field no source supplied returns ``(None, [not_found_provenance], 0.0)``
    (Req 6.3, 7.7).
    """
    gathered: list[tuple[Any, str, str | None, str | None, float]] = []
    for rec in records:
        fv = rec.values.get(field_name)
        if fv is None or fv.value is None:
            continue
        value, quality = normalizer(fv.value)
        if value is None:
            continue
        gathered.append((value, rec.source_type, rec.source_id, fv.method, quality))

    if not gathered:
        return None, [not_found_provenance(field_name)], 0.0

    containing = len(gathered)
    supplying: dict[Any, set[str | None]] = {}
    for value, _st, sid, _m, _q in gathered:
        supplying.setdefault(_value_key(value), set()).add(sid)

    candidates: list[CandidateValue] = []
    for value, source_type, sid, method, quality in gathered:
        agreement = agreement_score(len(supplying[_value_key(value)]), containing)
        candidates.append(
            CandidateValue(
                value=value,
                source_type=source_type,
                field_confidence=field_confidence(
                    reliability_of(source_type), agreement, quality
                ),
                normalization_quality=quality,
                source_id=sid,
                method=method,
            )
        )

    winner = select_winner(candidates)
    assert winner is not None  # non-empty by construction
    provenance = make_provenance(
        field_name,
        winner.value,
        winner.source_id,
        winner.method,
        winner.field_confidence,
    )
    return winner.value, [provenance], winner.field_confidence


def _value_key(value: Any) -> Any:
    """Return a hashable key for grouping candidate values (handles dataclasses)."""
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _merge_string_list(
    field_name: str,
    records: Iterable[PerSourceRecord],
    normalizer: _Normalizer,
) -> tuple[list[Any], list[ProvenanceEntry], float]:
    """Combine + dedup a list-valued string field (emails/phones) across sources.

    Each contributing value is normalized and scored (reliability + agreement +
    quality); :func:`combine_list_field` produces the deduplicated, deterministically
    ordered values and one provenance entry per contribution (Req 5.6, 6.4, 12.2).
    """
    gathered: list[tuple[Any, str, str | None, str | None, float]] = []
    containing = 0
    for rec in records:
        fv = rec.values.get(field_name)
        if fv is None or fv.value is None:
            continue
        kept: list[tuple[Any, float]] = []
        for raw in _iter_items(fv.value):
            value, quality = normalizer(raw)
            if value is None:
                continue
            kept.append((value, quality))
        if kept:
            containing += 1
        for value, quality in kept:
            gathered.append((value, rec.source_type, rec.source_id, fv.method, quality))

    if not gathered:
        return [], [not_found_provenance(field_name)], 0.0

    supplying: dict[Any, set[str | None]] = {}
    for value, _st, sid, _m, _q in gathered:
        supplying.setdefault(_value_key(value), set()).add(sid)

    contributions: list[ListContribution] = []
    confidences: list[float] = []
    for value, source_type, sid, method, quality in gathered:
        agreement = agreement_score(len(supplying[_value_key(value)]), containing)
        confidence = field_confidence(reliability_of(source_type), agreement, quality)
        confidences.append(confidence)
        contributions.append(
            ListContribution(
                value=value,
                source_type=source_type,
                source_id=sid,
                method=method,
                field_confidence=confidence,
            )
        )

    result = combine_list_field(field_name, contributions)
    representative = max(confidences) if confidences else 0.0
    return result.values, result.provenance, representative


def _merge_skills(
    records: Iterable[PerSourceRecord],
) -> tuple[list[Skill], list[str], list[ProvenanceEntry], float]:
    """Merge the list-valued ``skills`` field into canonical :class:`Skill` objects.

    Raw skills are normalized to ``Canonical_Skill_Name`` via the layered matcher
    (exact -> alias -> fuzzy). A value that matches nothing is **not** discarded: it
    is surfaced as an ``unknown_skill`` (deduplicated, deterministically ordered) so
    no extracted data is silently lost and a later vocabulary expansion can promote
    it to a recognized skill with no code change. Each recognized skill carries a
    Field_Confidence and its contributing source ids, and every contribution (known
    or unknown) keeps a provenance entry (Req 6.4).

    Returns ``(skills, unknown_skills, provenance, representative_confidence)``.
    """
    records = list(records)
    gathered: list[tuple[str, str, str | None, str | None, float]] = []
    # Unknown raw tokens, keyed by a normalized comparison key to dedupe variants
    # while preserving a representative display string and provenance lineage.
    unknown_display: dict[str, str] = {}
    unknown_provenance: list[ProvenanceEntry] = []
    containing = 0
    for rec in records:
        fv = rec.values.get("skills")
        if fv is None or fv.value is None:
            continue
        kept: list[tuple[str, float]] = []
        for raw in _iter_items(fv.value):
            name, quality = normalize_skill(raw)
            if name is None:
                # Out-of-vocabulary: keep it honestly as an unknown skill.
                if isinstance(raw, str):
                    display = " ".join(raw.split())
                    if display and any(ch.isalpha() for ch in display):
                        key = display.lower()
                        unknown_display.setdefault(key, display)
                        unknown_provenance.append(
                            make_provenance(
                                "unknown_skills", display, rec.source_id, fv.method, 0.0
                            )
                        )
                continue
            kept.append((name, quality))
        if kept:
            containing += 1
        for name, quality in kept:
            gathered.append((name, rec.source_type, rec.source_id, fv.method, quality))

    unknown_skills = sorted(unknown_display.values(), key=str.lower)

    if not gathered:
        provenance: list[ProvenanceEntry] = [not_found_provenance("skills")]
        provenance.extend(order_provenance(unknown_provenance))
        return [], unknown_skills, provenance, 0.0

    supplying: dict[str, set[str | None]] = {}
    quality_by_name: dict[str, float] = {}
    reliability_by_name: dict[str, float] = {}
    for name, source_type, sid, _m, quality in gathered:
        supplying.setdefault(name, set()).add(sid)
        quality_by_name[name] = max(quality_by_name.get(name, 0.0), quality)
        reliability_by_name[name] = max(
            reliability_by_name.get(name, 0.0), reliability_of(source_type)
        )

    skills: list[Skill] = []
    confidences: list[float] = []
    for name in sorted(supplying):
        agreement = agreement_score(len(supplying[name]), containing)
        confidence = field_confidence(
            reliability_by_name[name], agreement, quality_by_name[name]
        )
        confidences.append(confidence)
        sources = sorted(sid for sid in supplying[name] if sid is not None)
        skills.append(Skill(name=name, confidence=confidence, sources=sources))

    provenance_entries: list[ProvenanceEntry] = []
    for name, source_type, sid, method, quality in gathered:
        agreement = agreement_score(len(supplying[name]), containing)
        confidence = field_confidence(reliability_of(source_type), agreement, quality)
        provenance_entries.append(
            make_provenance("skills", name, sid, method, confidence)
        )
    provenance_entries.extend(unknown_provenance)

    representative = max(confidences) if confidences else 0.0
    return skills, unknown_skills, order_provenance(provenance_entries), representative


def _merge_links(
    records: Iterable[PerSourceRecord],
) -> tuple[Links, list[ProvenanceEntry], float]:
    """Assemble the canonical ``links`` structure from the group's sources.

    ``linkedin``/``github``/``portfolio`` are single-valued (winner-selected per
    subfield); ``other`` is combined + deduplicated like any list-valued field
    (Req 5.6). Returns the merged :class:`Links`, its provenance entries, and a
    representative confidence for overall scoring.
    """
    records = list(records)

    def _subfield(sub: str) -> tuple[str | None, ProvenanceEntry, float]:
        gathered: list[tuple[str, str, str | None, str | None]] = []
        for rec in records:
            fv = rec.values.get("links")
            if fv is None or not isinstance(fv.value, Links):
                continue
            value = getattr(fv.value, sub)
            if not value:
                continue
            gathered.append((value, rec.source_type, rec.source_id, fv.method))
        field_name = f"links.{sub}"
        if not gathered:
            return None, not_found_provenance(field_name), 0.0
        containing = len(gathered)
        supplying: dict[str, set[str | None]] = {}
        for value, _st, sid, _m in gathered:
            supplying.setdefault(value, set()).add(sid)
        candidates = [
            CandidateValue(
                value=value,
                source_type=source_type,
                field_confidence=field_confidence(
                    reliability_of(source_type),
                    agreement_score(len(supplying[value]), containing),
                    1.0,
                ),
                normalization_quality=1.0,
                source_id=sid,
                method=method,
            )
            for value, source_type, sid, method in gathered
        ]
        winner = select_winner(candidates)
        assert winner is not None
        return (
            winner.value,
            make_provenance(
                field_name, winner.value, winner.source_id, winner.method,
                winner.field_confidence,
            ),
            winner.field_confidence,
        )

    linkedin, prov_li, conf_li = _subfield("linkedin")
    github, prov_gh, conf_gh = _subfield("github")
    portfolio, prov_pf, conf_pf = _subfield("portfolio")

    # links.other -- list-valued.
    other_gathered: list[tuple[str, str, str | None, str | None]] = []
    other_containing = 0
    for rec in records:
        fv = rec.values.get("links")
        if fv is None or not isinstance(fv.value, Links):
            continue
        items = [item for item in (fv.value.other or []) if item is not None]
        if items:
            other_containing += 1
        for item in items:
            other_gathered.append((item, rec.source_type, rec.source_id, fv.method))

    if other_gathered:
        supplying_other: dict[str, set[str | None]] = {}
        for value, _st, sid, _m in other_gathered:
            supplying_other.setdefault(value, set()).add(sid)
        other_contribs = [
            ListContribution(
                value=value,
                source_type=source_type,
                source_id=sid,
                method=method,
                field_confidence=field_confidence(
                    reliability_of(source_type),
                    agreement_score(len(supplying_other[value]), other_containing),
                    1.0,
                ),
            )
            for value, source_type, sid, method in other_gathered
        ]
        other_result = combine_list_field("links.other", other_contribs)
        other_values = other_result.values
        other_provenance = other_result.provenance
        conf_other = max(c.field_confidence for c in other_contribs)
    else:
        other_values = []
        other_provenance = [not_found_provenance("links.other")]
        conf_other = 0.0

    links = Links(
        linkedin=linkedin,
        github=github,
        portfolio=portfolio,
        other=other_values,
    )
    provenance = [prov_li, prov_gh, prov_pf, *other_provenance]
    representative = max(conf_li, conf_gh, conf_pf, conf_other)
    return links, provenance, representative


def _merge_experience(
    records: Iterable[PerSourceRecord],
) -> tuple[list[ExperienceEntry], list[ProvenanceEntry], float]:
    """Combine experience entries across sources, normalizing dates and deduping.

    Entry dates are normalized to ``YYYY-MM`` (unparseable -> null, e.g. "Present").
    Entries are deduplicated by their full content and kept in deterministic
    first-seen order across the group's deterministically ordered records (Req 12).
    """
    seen_keys: set[tuple[Any, ...]] = set()
    entries: list[ExperienceEntry] = []
    provenance: list[ProvenanceEntry] = []
    confidences: list[float] = []
    for rec in records:
        fv = rec.values.get("experience")
        if fv is None or fv.value is None:
            continue
        for entry in _iter_items(fv.value):
            if not isinstance(entry, ExperienceEntry):
                continue
            start = normalize_date(entry.start)[0] if entry.start else None
            end = normalize_date(entry.end)[0] if entry.end else None
            normalized = ExperienceEntry(
                company=entry.company,
                title=entry.title,
                start=start,
                end=end,
                summary=entry.summary,
            )
            key = (
                normalized.company,
                normalized.title,
                normalized.start,
                normalized.end,
                normalized.summary,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(normalized)
            confidence = field_confidence(reliability_of(rec.source_type), 1.0, 1.0)
            confidences.append(confidence)
            provenance.append(
                make_provenance(
                    "experience", normalized, rec.source_id, fv.method, confidence
                )
            )

    if not entries:
        return [], [not_found_provenance("experience")], 0.0
    return entries, provenance, max(confidences)


def _merge_education(
    records: Iterable[PerSourceRecord],
) -> tuple[list[EducationEntry], list[ProvenanceEntry], float]:
    """Combine education entries across sources, deduping by content (Req 12)."""
    seen_keys: set[tuple[Any, ...]] = set()
    entries: list[EducationEntry] = []
    provenance: list[ProvenanceEntry] = []
    confidences: list[float] = []
    for rec in records:
        fv = rec.values.get("education")
        if fv is None or fv.value is None:
            continue
        for entry in _iter_items(fv.value):
            if not isinstance(entry, EducationEntry):
                continue
            key = (entry.institution, entry.degree, entry.field, entry.end_year)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(entry)
            confidence = field_confidence(reliability_of(rec.source_type), 1.0, 1.0)
            confidences.append(confidence)
            provenance.append(
                make_provenance(
                    "education", entry, rec.source_id, fv.method, confidence
                )
            )

    if not entries:
        return [], [not_found_provenance("education")], 0.0
    return entries, provenance, max(confidences)


def _build_canonical_record(
    candidate_id: str, records: list[PerSourceRecord]
) -> CanonicalRecord:
    """Merge one identity group's records into a scored :class:`CanonicalRecord`.

    Applies the Winner_Selection_Policy to single-valued fields, dedups list-valued
    fields, assembles nested structures, attaches provenance for every field (a
    "not found" entry for fields no source supplied), and computes the
    Overall_Confidence as the mean of the non-null field confidences (Req 5, 6, 7).
    """
    full_name, prov_name, conf_name = _merge_single(
        "full_name", records, _passthrough
    )
    emails, prov_emails, conf_emails = _merge_string_list(
        "emails", records, normalize_email
    )
    phones, prov_phones, conf_phones = _merge_string_list(
        "phones", records, normalize_phone
    )
    location_value, prov_location, conf_location = _merge_single(
        "location", records, _normalize_location
    )
    links, prov_links, conf_links = _merge_links(records)
    headline, prov_headline, conf_headline = _merge_single(
        "headline", records, _passthrough
    )
    years, prov_years, conf_years = _merge_single(
        "years_experience", records, _passthrough
    )
    skills, unknown_skills, prov_skills, conf_skills = _merge_skills(records)
    experience, prov_experience, conf_experience = _merge_experience(records)
    education, prov_education, conf_education = _merge_education(records)

    provenance: list[ProvenanceEntry] = []
    for chunk in (
        prov_name,
        prov_emails,
        prov_phones,
        prov_location,
        prov_links,
        prov_headline,
        prov_years,
        prov_skills,
        prov_experience,
        prov_education,
    ):
        provenance.extend(chunk)

    # Per-field confidences for Overall_Confidence. A field that *no* source
    # supplied is null: it is passed as ``None`` (not 0.0) so overall_confidence
    # excludes it as a missing field rather than averaging in a present-but-zero
    # score. Presence is derived from the merged value itself, so the null signal
    # is honest and independent of the numeric score (Req 7.6, 7.7).
    links_present = any(
        (links.linkedin, links.github, links.portfolio, links.other)
    )
    field_confidences = [
        conf_name if full_name is not None else None,
        conf_emails if emails else None,
        conf_phones if phones else None,
        conf_location if isinstance(location_value, Location) else None,
        conf_links if links_present else None,
        conf_headline if headline is not None else None,
        conf_years if years is not None else None,
        conf_skills if skills else None,
        conf_experience if experience else None,
        conf_education if education else None,
    ]

    return CanonicalRecord(
        candidate_id=candidate_id,
        full_name=full_name,
        emails=emails,
        phones=phones,
        location=location_value if isinstance(location_value, Location) else Location(),
        links=links,
        headline=headline,
        years_experience=years,
        skills=skills,
        unknown_skills=unknown_skills,
        experience=experience,
        education=education,
        provenance=provenance,
        overall_confidence=overall_confidence(field_confidences),
    )


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
class TransformerEngine:
    """Orchestrates the full pipeline from source references to projected profiles.

    A single instance is reusable across runs. The adapter registry is injectable
    for testing; by default the engine builds the standard registry encoding
    SourcePriority.
    """

    def __init__(self, registry: AdapterRegistry | None = None) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        #: Structured log entries from the most recent :meth:`run` (Req 11.3-11.6).
        self.logs: list[LogEntry] = []
        #: The canonical record(s) built by the most recent run, before projection.
        #: Exposed so callers can inspect/serialize the internal record that all
        #: projections are derived from (build once, project many).
        self.canonicals: list[CanonicalRecord] = []

    # -- public API --------------------------------------------------------

    def run(self, refs: Any, config: Any) -> RunResult:
        """Run the full pipeline, always returning a :class:`RunResult` (Req 10.5).

        ``refs`` is a list of :class:`SourceRef` (raw path/URL strings are accepted
        and converted). ``config`` is a :class:`ProjectionConfig` or a dict (parsed
        via :meth:`ProjectionConfig.from_dict`). No exception escapes: any failure
        is captured as an ``Error_Report`` and the run completes.

        Returns
        -------
        RunResult
            ``profiles`` (one per identity group), ``errors`` (every Error_Report),
            and ``exit_code`` (``0`` clean, non-zero when any error occurred).
        """
        logger = _RunLogger()
        self.logs = logger.entries
        errors: list[ErrorReport] = []
        profiles: list[dict[str, Any]] = []

        try:
            config_obj = self._coerce_config(config, errors, logger)
            source_refs = self._coerce_refs(refs, errors, logger)
            logger.info("engine", f"starting run over {len(source_refs)} source(s)")

            # Build the canonical record(s) once, then project with the single config.
            canonicals = self._assemble_canonicals(source_refs, errors, logger)
            self.canonicals = canonicals
            for record in canonicals:
                self._project_group(record, config_obj, profiles, errors, logger)
                logger.info(
                    "project", f"emitted profile for candidate {record.candidate_id}"
                )
        except Exception as exc:  # noqa: BLE001 - final safety net (Req 10.5, 18.1)
            errors.append(
                ErrorReport(
                    source=None,
                    stage="merge",
                    error=f"unexpected engine failure: {exc}",
                )
            )
            logger.error("engine", f"unexpected engine failure: {exc}")

        return RunResult(
            profiles=profiles,
            errors=errors,
            exit_code=0 if not errors else 1,
        )

    def run_multi(self, refs: Any, configs: list[Any]) -> list[RunResult]:
        """Build the canonical record(s) **once**, then project into each config.

        This is the "merge once, project many" entry point: Ingest -> Extract ->
        Normalize -> Resolve Identity -> Merge (and Confidence) run a **single time**
        to assemble the canonical record(s), and only the Project + Validate stages
        are repeated per config. It returns one :class:`RunResult` per config, in the
        same order as ``configs``.

        Every returned result carries the shared ingest/extract/merge errors (they
        happened once, so they apply to all projections) plus that config's own
        projection/validation errors. Like :meth:`run`, no exception escapes.
        """
        logger = _RunLogger()
        self.logs = logger.entries
        build_errors: list[ErrorReport] = []
        canonicals: list[CanonicalRecord] = []

        try:
            source_refs = self._coerce_refs(refs, build_errors, logger)
            logger.info(
                "engine",
                f"starting run over {len(source_refs)} source(s), "
                f"{len(configs)} projection(s)",
            )
            canonicals = self._assemble_canonicals(source_refs, build_errors, logger)
            self.canonicals = canonicals
        except Exception as exc:  # noqa: BLE001 - final safety net (Req 10.5, 18.1)
            build_errors.append(
                ErrorReport(
                    source=None,
                    stage="merge",
                    error=f"unexpected engine failure: {exc}",
                )
            )
            logger.error("engine", f"unexpected engine failure: {exc}")

        results: list[RunResult] = []
        for config in configs:
            # Each config starts from the shared build errors and adds its own.
            errors = list(build_errors)
            profiles: list[dict[str, Any]] = []
            try:
                config_obj = self._coerce_config(config, errors, logger)
                for record in canonicals:
                    self._project_group(record, config_obj, profiles, errors, logger)
                    logger.info(
                        "project",
                        f"emitted profile for candidate {record.candidate_id}",
                    )
            except Exception as exc:  # noqa: BLE001 - isolate a single config's failure
                errors.append(
                    ErrorReport(
                        source=None,
                        stage="project",
                        error=f"unexpected projection failure: {exc}",
                    )
                )
                logger.error("engine", f"unexpected projection failure: {exc}")
            results.append(
                RunResult(
                    profiles=profiles,
                    errors=errors,
                    exit_code=0 if not errors else 1,
                )
            )
        return results

    # -- input coercion ----------------------------------------------------

    @staticmethod
    def _coerce_config(
        config: Any, errors: list[ErrorReport], logger: _RunLogger
    ) -> ProjectionConfig:
        """Coerce ``config`` into a :class:`ProjectionConfig` (accepts dict or object)."""
        if isinstance(config, ProjectionConfig):
            return config
        if isinstance(config, dict):
            return ProjectionConfig.from_dict(config)
        if config is None:
            logger.warning("engine", "no projection config supplied; using empty config")
            return ProjectionConfig()
        # Unknown type: degrade to an empty config rather than crash.
        logger.warning(
            "engine",
            f"unrecognized projection config type {type(config).__name__}; "
            "using empty config",
        )
        return ProjectionConfig()

    @staticmethod
    def _coerce_refs(
        refs: Any, errors: list[ErrorReport], logger: _RunLogger
    ) -> list[SourceRef]:
        """Coerce ``refs`` into a list of :class:`SourceRef` (accepts strings/paths)."""
        if refs is None:
            return []
        if isinstance(refs, (str, os.PathLike, SourceRef)):
            refs = [refs]
        result: list[SourceRef] = []
        for ref in refs:
            if isinstance(ref, SourceRef):
                result.append(ref)
            elif isinstance(ref, (str, os.PathLike)):
                result.append(SourceRef(location=str(ref)))
            else:
                errors.append(
                    ErrorReport(
                        source=None,
                        stage="ingest",
                        error=f"unrecognized source reference {ref!r}",
                    )
                )
                logger.error("ingest", f"unrecognized source reference {ref!r}")
        return result

    # -- pipeline stages ---------------------------------------------------

    def _ingest_extract_normalize(
        self, ref: SourceRef, errors: list[ErrorReport], logger: _RunLogger
    ) -> list[PerSourceRecord]:
        """Ingest, extract, and normalize one source, isolating any failure.

        A missing/unresolvable source or an ingest failure becomes an ``ingest``
        error (Req 10.1); a malformed source becomes an ``extract`` error (Req 10.3);
        an empty source yields an all-null per-source record (Req 10.2). Returns the
        per-source records (possibly empty) without ever raising.
        """
        try:
            adapter = self.registry.resolve(ref)
        except NoAdapterFoundError as exc:
            errors.append(ErrorReport(ref.location, "ingest", str(exc)))
            logger.error("ingest", str(exc))
            return []

        try:
            raw = adapter.ingest(ref)
        except IngestError as exc:
            errors.append(ErrorReport(ref.location, "ingest", str(exc)))
            logger.error("ingest", str(exc))
            return []
        except Exception as exc:  # noqa: BLE001 - never let ingest crash the run
            errors.append(
                ErrorReport(ref.location, "ingest", f"unexpected ingest error: {exc}")
            )
            logger.error("ingest", f"unexpected ingest error: {exc}")
            return []

        logger.info("ingest", f"loaded {raw.source_type} source {raw.source_id!r}")

        try:
            extracted = adapter.extract(raw)
        except Exception as exc:  # noqa: BLE001 - malformed source, continue (Req 10.3)
            errors.append(
                ErrorReport(raw.source_id, "extract", f"failed to extract: {exc}")
            )
            logger.error("extract", f"failed to extract {raw.source_id!r}: {exc}")
            return []

        # Surface any per-record extract errors the adapter captured (Req 10.3, 11.6).
        for record in extracted:
            for err in record.errors:
                errors.append(err)
                logger.error("extract", err.error)

        if not extracted:
            logger.warning(
                "extract",
                f"source {raw.source_id!r} produced no records; using all-null record",
            )
            extracted = [new_null_per_source_record(raw.source_id, raw.source_type)]

        logger.info(
            "normalize",
            f"extracted {len(extracted)} record(s) from {raw.source_id!r}",
        )
        return extracted

    def _resolve_identity(
        self,
        records: list[PerSourceRecord],
        errors: list[ErrorReport],
        logger: _RunLogger,
    ) -> list[list[PerSourceRecord]]:
        """Group records into identity groups, degrading to singletons on failure."""
        try:
            groups = group(records)
        except Exception as exc:  # noqa: BLE001 - never crash on resolution
            errors.append(
                ErrorReport(
                    source=None,
                    stage="resolve",
                    error=f"identity resolution failed: {exc}",
                )
            )
            logger.error("resolve", f"identity resolution failed: {exc}")
            return [[record] for record in records]
        logger.info("resolve", f"resolved {len(groups)} identity group(s)")
        return groups

    def _assemble_canonicals(
        self,
        source_refs: list[SourceRef],
        errors: list[ErrorReport],
        logger: _RunLogger,
    ) -> list[CanonicalRecord]:
        """Run Ingest -> ... -> Merge once, returning the canonical record(s).

        This is the config-independent half of the pipeline shared by :meth:`run`
        and :meth:`run_multi`. It ingests/extracts/normalizes every source, resolves
        identity groups, and merges each group into a scored :class:`CanonicalRecord`.

        Cross-cutting guarantees are preserved: a per-candidate merge failure is
        isolated as an ``Error_Report`` and the remaining candidates continue
        (Req 14.3, 14.4); when *no* per-source records are available at all, a single
        all-null canonical record is returned so the run still yields a structurally
        valid profile (Req 10.4).
        """
        records: list[PerSourceRecord] = []
        for ref in source_refs:
            records.extend(self._ingest_extract_normalize(ref, errors, logger))

        if not records:
            # Every source failed/empty (or no input) -> all-null record (Req 10.4).
            logger.warning(
                "merge",
                "no per-source records available; emitting an all-null record",
            )
            return [new_null_canonical_record()]

        groups = self._resolve_identity(records, errors, logger)
        canonicals: list[CanonicalRecord] = []
        for candidate_id, group_records in assign_candidate_ids(groups):
            try:
                canonicals.append(
                    _build_canonical_record(candidate_id, group_records)
                )
            except Exception as exc:  # noqa: BLE001 - per-candidate isolation (Req 14.4)
                errors.append(
                    ErrorReport(
                        source=None,
                        stage="merge",
                        error=f"candidate {candidate_id} failed during merge: {exc}",
                    )
                )
                logger.error("merge", f"candidate {candidate_id} failed: {exc}")
        return canonicals

    @staticmethod
    def _project_group(
        record: CanonicalRecord,
        config: ProjectionConfig,
        profiles: list[dict[str, Any]],
        errors: list[ErrorReport],
        logger: _RunLogger,
    ) -> None:
        """Project + validate one canonical record, collecting projection/validation errors."""
        try:
            profile, projection_errors = project(record, config)
        except Exception as exc:  # noqa: BLE001 - isolate projection failures
            errors.append(
                ErrorReport(
                    source=None,
                    stage="project",
                    error=f"projection failed: {exc}",
                )
            )
            logger.error("project", f"projection failed: {exc}")
            return

        for err in projection_errors:
            errors.append(err)
            logger.error("project", err.error)

        try:
            validation_errors = validate(profile, config)
        except Exception as exc:  # noqa: BLE001 - isolate validation failures
            errors.append(
                ErrorReport(
                    source=None,
                    stage="validate",
                    error=f"validation failed: {exc}",
                )
            )
            logger.error("validate", f"validation failed: {exc}")
            validation_errors = []

        for verr in validation_errors:
            report = verr.to_error_report()
            errors.append(report)
            logger.error("validate", report.error)

        profiles.append(profile)


def run(refs: Any, config: Any, *, registry: AdapterRegistry | None = None) -> RunResult:
    """Module-level convenience wrapper over :meth:`TransformerEngine.run`."""
    return TransformerEngine(registry=registry).run(refs, config)

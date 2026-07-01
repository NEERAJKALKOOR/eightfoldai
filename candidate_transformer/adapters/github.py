"""GitHub profile source adapter (unstructured).

Given a **public GitHub profile** (Req 1.5), this adapter extracts the candidate
signals a profile reliably carries: the ``username`` (login), the display
``name`` (-> ``full_name``), the ``bio`` (-> ``headline``), the free-text
``location``, the set of programming languages used across the profile's
repositories (-> candidate ``skills``), and the profile link itself
(-> ``links.github``). The design's "Unstructured Source Adapters" entry names
exactly this flow ("from a public profile URL, extracts username, name, bio
(-> headline), location, repo languages (-> candidate skills), and the profile
link").

Deterministic, offline operation
--------------------------------
Live scraping is non-deterministic and unavailable in this environment, so the
adapter operates on a **fetched/sample profile payload** -- a JSON document (the
shape a GitHub profile API response takes) referenced by the
:class:`~.base.SourceRef`. The payload is read from a local file so identical
inputs always produce identical output (Req 12). The recognized URL hosts let the
registry route a ``github.com`` reference to this adapter; for offline runs the
caller points ``ref.location`` at the local JSON payload and tags it with
``source_type="github"``.

Faithful extraction, not normalization
--------------------------------------
The adapter records exactly what the payload contains (minus surrounding
whitespace) together with the extraction ``method`` for each value, and **never
invents a value** (Req 2.4, 2.5, 2.6). Canonical formatting (``Canonical_Skill_Name``
skills, ISO country codes, ...) is the downstream Normalize stage's job. A field
the payload omits is recorded as a null :class:`FieldValue`.

GitHub is a relatively low-authority source (priority rank 4, reliability 0.70);
both values come straight from the shared tables in :mod:`.base`.

Robustness (Req 10.3, 18.1): a missing/unreadable file raises :class:`IngestError`
at the ingest boundary (the only exception ``ingest`` may raise); malformed JSON is
detected during ``ingest`` so it fails fast, and ``extract`` is additionally
defensive -- if handed unparseable content it records a structured ``extract``-stage
error rather than crashing.
"""

from __future__ import annotations

import json

from candidate_transformer.models import (
    ErrorReport,
    FieldValue,
    Links,
    Location,
    PerSourceRecord,
)

from .base import (
    IngestError,
    RawSource,
    SourceRef,
    priority_of,
    reliability_of,
)

__all__ = ["GithubAdapter"]

SOURCE_TYPE = "github"

# Extraction-method tags recorded for provenance (Req 2.5).
_METHOD_NAME = "github_profile_name"
_METHOD_HEADLINE = "github_profile_bio"
_METHOD_LOCATION = "github_profile_location"
_METHOD_SKILLS = "github_repo_languages"
_METHOD_LINKS = "github_profile_url"
_METHOD_EMAIL = "github_profile_email"


class GithubAdapter:
    """Adapter for the GitHub profile source -- unstructured (Req 1.5).

    Satisfies the :class:`~.base.SourceAdapter` protocol. A single instance is
    stateless and reusable across references.
    """

    source_type: str = SOURCE_TYPE
    reliability: float = reliability_of(SOURCE_TYPE)
    priority: int = priority_of(SOURCE_TYPE)

    # -- recognition -------------------------------------------------------

    def can_handle(self, ref: SourceRef) -> bool:
        """True for an explicit ``github`` hint or a ``github.com`` URL location.

        A bare ``.json`` payload is only recognized through the explicit
        ``source_type="github"`` hint so it does not collide with the ATS JSON
        adapter (which claims ``.json`` by extension).
        """
        if ref.source_type == SOURCE_TYPE:
            return True
        if ref.source_type is not None:
            return False
        return "github.com" in ref.location.lower()

    # -- ingest ------------------------------------------------------------

    def ingest(self, ref: SourceRef) -> RawSource:
        """Read the local GitHub profile payload (JSON) referenced by ``ref``.

        Operates offline on a fetched/sample payload file: any read/decode failure
        is surfaced as :class:`IngestError` (the only exception ``ingest`` may
        raise) so the runner records an ``ingest``-stage error and continues
        (Req 10.1, 10.3). Malformed JSON is detected here so a corrupt payload fails
        fast. ``source_id`` is ``ref.location`` (Req 1.9).
        """
        try:
            with open(ref.location, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(
                f"failed to read GitHub profile payload {ref.location!r}: {exc}"
            ) from exc

        # Validate parseability up front so a malformed payload fails fast.
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"GitHub profile payload {ref.location!r} is not valid JSON: {exc}"
            ) from exc

        return RawSource(
            source_id=ref.location,
            source_type=SOURCE_TYPE,
            content=text,
            ref=ref,
        )

    # -- extract -----------------------------------------------------------

    def extract(self, raw: RawSource) -> list[PerSourceRecord]:
        """Parse the profile payload into a single :class:`PerSourceRecord` (Req 2.1).

        A GitHub profile describes exactly one candidate, so this always returns one
        record. Fields the payload omits are recorded as null :class:`FieldValue` s
        -- never invented (Req 2.4, 2.6). Malformed content is captured as an
        ``extract``-stage error instead of crashing (Req 10.3, 18.1).
        """
        text = raw.content
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if not isinstance(text, str):
            text = ""

        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            record = PerSourceRecord.empty(raw.source_id, raw.source_type)
            record.errors.append(
                ErrorReport(
                    source=raw.source_id,
                    stage="extract",
                    error=f"Malformed GitHub profile payload: {exc}",
                )
            )
            return [record]

        if not isinstance(data, dict):
            record = PerSourceRecord.empty(raw.source_id, raw.source_type)
            record.errors.append(
                ErrorReport(
                    source=raw.source_id,
                    stage="extract",
                    error=f"Expected a JSON object, got {type(data).__name__}",
                )
            )
            return [record]

        username = self._clean_str(data.get("login") or data.get("username"))

        values: dict[str, FieldValue] = {}

        # -- name -> full_name --------------------------------------------
        values["full_name"] = FieldValue(
            value=self._clean_str(data.get("name")), method=_METHOD_NAME
        )

        # -- bio -> headline ----------------------------------------------
        values["headline"] = FieldValue(
            value=self._clean_str(data.get("bio")), method=_METHOD_HEADLINE
        )

        # -- location -----------------------------------------------------
        values["location"] = FieldValue(
            value=self._parse_location(data.get("location")),
            method=_METHOD_LOCATION,
        )

        # -- email (optional; public profiles may expose one) -------------
        values["emails"] = FieldValue(
            value=self._clean_str(data.get("email")), method=_METHOD_EMAIL
        )

        # -- repo languages -> skills -------------------------------------
        values["skills"] = FieldValue(
            value=self._extract_languages(data), method=_METHOD_SKILLS
        )

        # -- profile link -> links.github ---------------------------------
        profile_url = self._profile_url(data, username)
        values["links"] = FieldValue(
            value=Links(github=profile_url) if profile_url else None,
            method=_METHOD_LINKS,
        )

        return [
            PerSourceRecord(
                source_id=raw.source_id,
                source_type=raw.source_type,
                values=values,
            )
        ]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _clean_str(value: object) -> str | None:
        """Return a whitespace-trimmed non-empty string, else ``None`` (never invents)."""
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @classmethod
    def _profile_url(cls, data: dict, username: str | None) -> str | None:
        """Return the canonical profile link from the payload, or derive it.

        Prefers an explicit ``html_url``/``profile_url`` in the payload; otherwise
        derives ``https://github.com/<username>`` from the login. Returns ``None``
        when neither is available -- the link is never invented.
        """
        explicit = cls._clean_str(data.get("html_url") or data.get("profile_url"))
        if explicit:
            return explicit
        if username:
            return f"https://github.com/{username}"
        return None

    @classmethod
    def _extract_languages(cls, data: dict) -> list[str] | None:
        """Return the distinct repository languages as a raw skill list, or ``None``.

        Accepts either a flat ``languages`` list or a ``repositories`` list whose
        elements each carry a ``language``. Languages are returned verbatim (the
        Normalize stage maps them to ``Canonical_Skill_Name``), de-duplicated with
        first-seen order preserved for determinism (Req 12.2).
        """
        languages: list[str] = []

        flat = data.get("languages")
        if isinstance(flat, list):
            for lang in flat:
                cleaned = cls._clean_str(lang)
                if cleaned:
                    languages.append(cleaned)

        repos = data.get("repositories") or data.get("repos")
        if isinstance(repos, list):
            for repo in repos:
                if isinstance(repo, dict):
                    cleaned = cls._clean_str(repo.get("language"))
                    if cleaned:
                        languages.append(cleaned)

        # De-duplicate case-insensitively, preserving first-seen order.
        seen: set[str] = set()
        deduped: list[str] = []
        for lang in languages:
            key = lang.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(lang)
        return deduped or None

    @classmethod
    def _parse_location(cls, value: object) -> Location | None:
        """Parse a free-text ``"City, Region, Country"`` string into a :class:`Location`.

        GitHub stores location as free text. The string is split on commas into up
        to city / region / country; a single token is treated as the city. Returns
        ``None`` when no location is present -- never invents location parts.
        """
        text = cls._clean_str(value)
        if not text:
            return None
        pieces = [p.strip() for p in text.split(",") if p.strip()]
        if not pieces:
            return None
        if len(pieces) >= 3:
            return Location(city=pieces[0], region=pieces[1], country=pieces[2])
        if len(pieces) == 2:
            return Location(city=pieces[0], country=pieces[1])
        return Location(city=pieces[0])

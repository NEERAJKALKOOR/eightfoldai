"""Example unit tests for the Resume adapter (Task 5.6).

A known resume fixture (the plain-text resume driven through the same section
parsers, plus the DOCX document) yields a single ``PerSourceRecord`` with the
correct ``source_type``/``source_id`` (Req 1.9) and the documented section/regex
extraction: contact info, skills, experience, and education (Req 1.7). Each value
records its extraction method for provenance, including ``pdf_section_skills``
(Req 2.5); absent fields are recorded null, never invented (Req 2.4, 2.6).

_Requirements: 1.7, 1.9, 2.5_
"""

from __future__ import annotations

from pathlib import Path

import pytest

from candidate_transformer.adapters import ResumeAdapter, build_default_registry
from candidate_transformer.adapters.base import (
    SourceRef,
    priority_of,
    reliability_of,
)
from candidate_transformer.models import Links, Location, PerSourceRecord

FIXTURE_TXT = Path(__file__).resolve().parent / "fixtures" / "resume_jane_doe.txt"
FIXTURE_DOCX = Path(__file__).resolve().parent / "fixtures" / "resume_jane_doe.docx"


@pytest.fixture()
def adapter() -> ResumeAdapter:
    return ResumeAdapter()


def _extract_txt(adapter: ResumeAdapter) -> PerSourceRecord:
    # A .txt resume is driven through the section parsers via the explicit hint.
    raw = adapter.ingest(SourceRef(location=str(FIXTURE_TXT), source_type="resume"))
    records = adapter.extract(raw)
    assert len(records) == 1  # an unstructured source describes one candidate
    return records[0]


# -- attributes / provenance anchors --------------------------------------


def test_adapter_attributes_come_from_shared_tables(adapter: ResumeAdapter) -> None:
    assert adapter.source_type == "resume"
    assert adapter.reliability == reliability_of("resume") == 0.85
    assert adapter.priority == priority_of("resume") == 2


def test_source_provenance_recorded(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    assert rec.source_type == "resume"
    assert rec.source_id == str(FIXTURE_TXT)


# -- contact info via regex (Req 1.7) -------------------------------------


def test_contact_info_extracted(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    assert rec.values["full_name"].value == "JANE DOE"
    assert rec.values["emails"].value == "jane.doe@example.com"
    # Phone captured verbatim (E.164 conversion is the Normalize stage's job).
    assert rec.values["phones"].value == "+1 415-555-2671"
    assert rec.values["location"].value == Location(
        city="San Francisco", region="CA", country="USA"
    )


def test_profile_links_extracted(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    assert rec.values["links"].value == Links(
        linkedin="linkedin.com/in/janedoe", github="github.com/janedoe"
    )


# -- skills section (Req 1.7, 2.5) ----------------------------------------


def test_skills_section_extracted_verbatim_with_method(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    assert rec.values["skills"].value == [
        "py",
        "JS",
        "Docker",
        "Kubernetes",
        "PostgreSQL",
    ]
    assert rec.values["skills"].method == "pdf_section_skills"


# -- experience / education sections --------------------------------------


def test_experience_section_parsed_in_order(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    exp = rec.values["experience"].value
    assert exp[0].company == "Acme Corporation"
    assert exp[0].title == "Senior Software Engineer"
    assert exp[0].start == "March 2019"
    assert exp[0].end == "Present"
    assert exp[1].company == "Globex Systems"
    assert exp[1].title == "Software Engineer"
    # Headline is the most recent role's title.
    assert rec.values["headline"].value == "Senior Software Engineer"


def test_education_section_parsed(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    edu = rec.values["education"].value
    assert edu[0].institution == "University of California, Berkeley"
    assert edu[0].degree == "B.S."
    assert edu[0].field == "Computer Science"
    assert edu[0].end_year == 2015


def test_extraction_methods_recorded(adapter: ResumeAdapter) -> None:
    rec = _extract_txt(adapter)
    assert rec.values["emails"].method == "regex_email"
    assert rec.values["phones"].method == "regex_phone"
    assert rec.values["skills"].method == "pdf_section_skills"


# -- DOCX document path (Req 1.7) -----------------------------------------


def test_docx_document_extracts_core_fields(adapter: ResumeAdapter) -> None:
    raw = adapter.ingest(SourceRef(location=str(FIXTURE_DOCX)))
    rec = adapter.extract(raw)[0]
    assert rec.source_type == "resume"
    assert rec.source_id == str(FIXTURE_DOCX)
    # The DOCX describes the same candidate; contact info should be recovered.
    assert rec.values["emails"].value == "jane.doe@example.com"
    assert rec.values["full_name"].value is not None


# -- registry routing ------------------------------------------------------


def test_default_registry_resolves_pdf_and_docx() -> None:
    registry = build_default_registry()
    assert isinstance(
        registry.resolve(SourceRef(location="resume.pdf")), ResumeAdapter
    )
    assert isinstance(
        registry.resolve(SourceRef(location="resume.docx")), ResumeAdapter
    )


# -- education / location robustness on messy PDF-style input -------------


def test_education_stops_at_next_section_and_drops_noise() -> None:
    """Education ignores the following Projects block, GPA/score noise, and bullets.

    Mirrors the structure of a real PDF resume where the Education section is
    immediately followed by Projects with no clean separator. Only the two real
    institution lines should survive (Req 2.6 -- omit noise, never invent).
    """
    edu_lines = [
        "Artificial Intelligence and Data Science Engineering 2023 - 2027",
        "GPA: 9.2",
        "M.S. Ramaiah Institute of Technology, Bangalore",
        "II PU 2023",
        "Score: 98.67%",
        "Mahesh PU College, Bangalore",
        "Projects",
        "BharatStore (Namma Kirani)",
        "\u2022 Built an offline-first Flutter application for kirana stores.",
        "NewSphere 2025",
        "\u2022 Developed an intelligent news aggregation system.",
    ]
    entries = ResumeAdapter._extract_education(edu_lines)
    institutions = [e.institution for e in entries]
    assert institutions == [
        "M.S. Ramaiah Institute of Technology, Bangalore",
        "Mahesh PU College, Bangalore",
    ]


def test_education_pairs_degree_detail_line() -> None:
    """An institution line followed by a degree/field detail is paired into one entry."""
    entries = ResumeAdapter._extract_education(
        ["University of California, Berkeley", "B.S. in Computer Science, 2015"]
    )
    assert len(entries) == 1
    assert entries[0].institution == "University of California, Berkeley"
    assert entries[0].degree == "B.S."
    assert entries[0].field == "Computer Science"
    assert entries[0].end_year == 2015


def test_location_rejects_sentence_prose() -> None:
    """A comma-bearing summary sentence is not mistaken for a location (stays null)."""
    header = [
        "Jane Doe",
        "Adaptable engineering student with strong foundations in algorithms, "
        "data structures, and computer networks.",
    ]
    assert ResumeAdapter._extract_location(header) is None


def test_location_accepts_short_place_fragment() -> None:
    """A genuine short "City, Region, Country" fragment is still parsed."""
    loc = ResumeAdapter._extract_location(["San Francisco, CA, USA"])
    assert loc == Location(city="San Francisco", region="CA", country="USA")

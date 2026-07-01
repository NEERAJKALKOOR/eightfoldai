"""Unit tests for the skills normalizer and Controlled_Skill_Vocabulary.

Covers Req 3.7 (alias -> canonical mapping), Req 3.8 (out-of-vocabulary -> null,
null-honesty), and Req 3.10 (Normalization_Quality in [0, 1]).
"""

from __future__ import annotations

import pytest

from candidate_transformer.normalizers import (
    Controlled_Skill_Vocabulary,
    normalize_skill,
)


class TestExactCanonicalMatch:
    """A raw value equal to a canonical name scores 1.0 (case-insensitive)."""

    @pytest.mark.parametrize(
        "raw,canonical",
        [
            ("Python", "Python"),
            ("python", "Python"),
            ("PYTHON", "Python"),
            ("JavaScript", "JavaScript"),
            ("kubernetes", "Kubernetes"),
            ("Kubernetes", "Kubernetes"),
            ("Docker", "Docker"),
            ("PostgreSQL", "PostgreSQL"),
            ("Go", "Go"),
            ("Rust", "Rust"),
            ("Java", "Java"),
            ("Terraform", "Terraform"),
        ],
    )
    def test_canonical_name_scores_one(self, raw: str, canonical: str) -> None:
        assert normalize_skill(raw) == (canonical, 1.0)


class TestAliasMatch:
    """A raw value matched via an alias scores 0.8."""

    @pytest.mark.parametrize(
        "raw,canonical",
        [
            ("py", "Python"),
            ("python3", "Python"),
            ("Python Programming", "Python"),
            ("js", "JavaScript"),
            ("JS", "JavaScript"),
            ("Java Script", "JavaScript"),
            ("k8s", "Kubernetes"),
            ("postgres", "PostgreSQL"),
            ("golang", "Go"),
            ("tf", "Terraform"),
        ],
    )
    def test_alias_scores_point_eight(self, raw: str, canonical: str) -> None:
        assert normalize_skill(raw) == (canonical, 0.8)

    def test_alias_equal_to_canonical_name_scores_one(self) -> None:
        # "javascript" lowercased equals the canonical "JavaScript", so it is a
        # case-insensitive exact match (1.0), not an alias match.
        assert normalize_skill("javascript") == ("JavaScript", 1.0)


class TestWhitespaceInsensitive:
    """Surrounding and internal whitespace must not affect matching."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  Python  ", ("Python", 1.0)),
            ("\tPython\n", ("Python", 1.0)),
            ("python   programming", ("Python", 0.8)),
            ("  java   script ", ("JavaScript", 0.8)),
        ],
    )
    def test_whitespace_collapsed(self, raw: str, expected: tuple) -> None:
        assert normalize_skill(raw) == expected


class TestOutOfVocabulary:
    """Unknown skills return (None, 0.0) and never invent a canonical name."""

    @pytest.mark.parametrize(
        "raw",
        ["wizardry", "telekinesis", "", "   ", "qwerty12345"],
    )
    def test_unknown_returns_null(self, raw: str) -> None:
        assert normalize_skill(raw) == (None, 0.0)


class TestBadInputNeverRaises:
    """Non-string / odd inputs resolve to (None, 0.0) without raising."""

    @pytest.mark.parametrize("raw", [None, 42, 3.14, [], {}, object()])
    def test_bad_input(self, raw: object) -> None:
        assert normalize_skill(raw) == (None, 0.0)


class TestDeterminism:
    """Repeated calls yield identical results (pure function)."""

    def test_repeated_calls_identical(self) -> None:
        assert normalize_skill("py") == normalize_skill("py")
        assert normalize_skill("  PYTHON  ") == normalize_skill("python")


class TestVocabularyShape:
    """The vocabulary includes the required examples and quality bounds hold."""

    def test_includes_required_examples(self) -> None:
        assert "Python" in Controlled_Skill_Vocabulary
        assert "JavaScript" in Controlled_Skill_Vocabulary
        py_aliases = {a.lower() for a in Controlled_Skill_Vocabulary["Python"]}
        assert {"py", "python3", "python programming"} <= py_aliases
        js_aliases = {a.lower() for a in Controlled_Skill_Vocabulary["JavaScript"]}
        assert {"js", "javascript", "java script"} <= js_aliases

    def test_quality_in_unit_interval(self) -> None:
        for raw in ["Python", "py", "wizardry", "", None, "k8s"]:
            _, quality = normalize_skill(raw)
            assert 0.0 <= quality <= 1.0

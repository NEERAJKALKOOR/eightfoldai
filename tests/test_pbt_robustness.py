"""Property-based test for pipeline robustness (graceful degradation).

Feature: candidate-data-transformer, Property 2

Property 2: Robustness -- never crashes on garbage.

*For any* input -- including missing references, empty files, random bytes,
malformed JSON/CSV, and truncated documents -- the run completes without raising,
returns a ``RunResult``, and reports a structured ``Error_Report`` (with
``{source, stage, error}``) for each source that failed.

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 18.1**
"""

from __future__ import annotations

import json
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from candidate_transformer.adapters import SourceRef
from candidate_transformer.engine.projection import ProjectionConfig
from candidate_transformer.engine.transformer import TransformerEngine
from candidate_transformer.models import ErrorReport, RunResult

# The fixed set of pipeline stages an Error_Report may be tagged with (Req 11.2).
KNOWN_STAGES = frozenset(
    {"ingest", "extract", "normalize", "resolve", "merge", "project", "validate"}
)

# Varied extensions so the adapter registry routes to different adapters
# (CSV/JSON/text/resume) -- exercising each adapter's parse path with garbage.
EXTENSIONS = (".csv", ".json", ".txt", ".pdf", ".docx")

# A realistic projection config (mirrors samples/configs/default.json) so the
# projection + validation stages also run against the degraded canonical records.
_CONFIG = ProjectionConfig.from_dict(
    {
        "include_provenance": False,
        "include_confidence": True,
        "fields": [
            {"name": "id", "from": "candidate_id", "type": "string",
             "required": True, "on_missing": "error"},
            {"name": "name", "from": "full_name", "type": "string",
             "on_missing": "null"},
            {"name": "primary_email", "from": "emails[0]", "type": "string",
             "on_missing": "null"},
            {"name": "skills", "from": "skills[].name", "type": "array",
             "element_type": "string", "on_missing": "null"},
        ],
    }
)


# --------------------------------------------------------------------------- #
# Adversarial content strategies
# --------------------------------------------------------------------------- #
def _malformed_json() -> st.SearchStrategy[bytes]:
    """Text that looks JSON-ish but is malformed or truncated."""
    return st.sampled_from(
        [
            b"{",
            b"{ \"candidateName\": ",
            b"[ { \"a\": 1 }, ",
            b"{ 'single': 'quotes' }",
            b"{ \"unterminated\": \"string ",
            b"not json at all",
            b"\xff\xfe\x00\x01garbage",
            b"null",
            b"[1, 2, 3",
            b"{ \"n\": NaN }",
        ]
    )


def _malformed_csv() -> st.SearchStrategy[bytes]:
    """CSV with broken/quoted/ragged structure or wrong headers."""
    return st.sampled_from(
        [
            b"a,b,c\n1,2",  # ragged row
            b'name,email\n"unterminated,quote',  # unterminated quote
            b"\x00\x01\x02binary,in,csv",  # binary bytes
            b"only_one_column",  # no expected headers
            b"name;email;phone\n;;",  # wrong delimiter
            b",,,\n,,,",  # all-empty fields
        ]
    )


def _garbage_bytes() -> st.SearchStrategy[bytes]:
    """Arbitrary bytes / text / truncated documents."""
    return st.one_of(
        st.binary(min_size=0, max_size=300),
        st.text(max_size=300).map(lambda s: s.encode("utf-8", "ignore")),
        _malformed_json(),
        _malformed_csv(),
        st.just(b"%PDF-1.4 truncated not really a pdf"),  # fake/truncated PDF
        st.just(b"PK\x03\x04 truncated not really a docx"),  # fake/truncated DOCX
    )


# Each source spec is (kind, extension, content_bytes). "missing" references a
# non-existent path, "empty" writes a zero-byte file, "garbage" writes adversarial
# bytes.
_source_spec = st.tuples(
    st.sampled_from(["missing", "empty", "garbage"]),
    st.sampled_from(EXTENSIONS),
    _garbage_bytes(),
)


# --------------------------------------------------------------------------- #
# Property 2
# --------------------------------------------------------------------------- #
@given(specs=st.lists(_source_spec, min_size=0, max_size=5))
@settings(deadline=None)
def test_robustness_never_crashes_on_garbage(specs):
    """Feature: candidate-data-transformer, Property 2.

    The run completes without raising, returns a RunResult, every Error_Report has
    the {source, stage, error} shape with a known stage, and the exit code is
    non-zero whenever any error occurred. Missing references always surface an error.
    """
    engine = TransformerEngine()

    with tempfile.TemporaryDirectory() as tmp:
        refs: list[SourceRef] = []
        has_missing = False
        for index, (kind, ext, content) in enumerate(specs):
            if kind == "missing":
                has_missing = True
                # Point at a path that is never created.
                refs.append(
                    SourceRef(location=os.path.join(tmp, f"missing_{index}{ext}"))
                )
                continue

            path = os.path.join(tmp, f"src_{index}{ext}")
            if kind == "empty":
                with open(path, "wb"):
                    pass  # zero-byte file
            else:  # garbage
                with open(path, "wb") as handle:
                    handle.write(content)
            refs.append(SourceRef(location=path))

        # The run must never raise -- a crash here fails the property (Req 10.5, 18.1).
        try:
            result = engine.run(refs, _CONFIG)
        except Exception as exc:  # noqa: BLE001 - surfacing a crash is the failure
            raise AssertionError(
                f"engine.run raised on adversarial input {specs!r}: {exc!r}"
            ) from exc

        # Always returns a structured RunResult (Req 10.5, 18.1).
        assert isinstance(result, RunResult)
        assert isinstance(result.errors, list)
        assert isinstance(result.profiles, list)

        # Every reported error has the structured {source, stage, error} shape with a
        # known pipeline stage (Req 11.1, 11.2).
        for err in result.errors:
            assert isinstance(err, ErrorReport)
            assert err.stage in KNOWN_STAGES, f"unexpected stage {err.stage!r}"
            assert err.source is None or isinstance(err.source, str)
            assert isinstance(err.error, str) and err.error

        # The exit code is non-zero exactly-when errors were reported (Req 10.4, 13.7).
        if result.errors:
            assert result.exit_code != 0
        else:
            assert result.exit_code == 0

        # A missing referenced source must always be reported as a failure that
        # names the source and is tagged to the ingest stage (Req 10.1).
        if has_missing:
            assert result.errors, "missing source did not produce an Error_Report"
            assert any(e.stage == "ingest" for e in result.errors)

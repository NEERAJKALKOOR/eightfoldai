"""Command-line interface for the candidate data transformer (Req 13).

This module implements the ``candidate-transform`` console script declared in
``pyproject.toml``. It is a **thin shell** over
:meth:`candidate_transformer.engine.TransformerEngine.run`: it parses arguments,
loads the projection config, hands ``SourceRef``s and the config to the engine, and
serializes the resulting profiles. All business logic lives in the engine library so
the CLI carries none of its own (Req 13, design "CLI Surface").

Behavior:

* one or more ``--input`` references plus one ``--config`` run the pipeline and emit
  the projected profiles as JSON (Req 13.1);
* ``--output PATH`` writes the JSON to that file, otherwise it prints to stdout
  (Req 13.2, 13.3);
* a missing required ``--input`` is a usage error naming the argument, with a
  non-zero exit (Req 13.4);
* an unparseable ``--config`` is a configuration error naming the problem, with a
  non-zero exit (Req 13.5);
* a clean run exits ``0`` (Req 13.6); a run with any source/projection error exits
  non-zero and prints the ``Error_Report`` entries to stderr (Req 13.7).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from candidate_transformer.adapters import SOURCE_PRIORITY, SourceRef
from candidate_transformer.engine import TransformerEngine
from candidate_transformer.engine.projection import ProjectionConfig, ProjectionConfigError
from candidate_transformer.models import to_dict

# Process exit codes (Req 13.4, 13.5, 13.6, 13.7).
_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_CONFIG = 2
_EXIT_RUN_ERRORS = 1

# The known source-type hints a caller may prefix onto an --input (e.g.
# "github=path"). Lets a local JSON payload be routed to the GitHub/LinkedIn
# adapters, which otherwise only trigger on a URL location or an explicit hint.
_SOURCE_TYPES = frozenset(SOURCE_PRIORITY)


def _parse_input_ref(raw: str) -> SourceRef:
    """Parse one ``--input`` value into a :class:`SourceRef`.

    Supports an optional ``type=path`` prefix where ``type`` is one of the known
    source types (``github``, ``linkedin``, ``ats_json``, ``recruiter_csv``,
    ``resume``, ``recruiter_notes``). The ``=`` separator avoids clashing with
    Windows drive-letter colons. Without a recognized prefix the whole value is
    treated as a path and the adapter is chosen by extension/URL.
    """
    prefix, sep, rest = raw.partition("=")
    if sep and prefix in _SOURCE_TYPES:
        return SourceRef(location=rest, source_type=prefix)  # type: ignore[arg-type]
    return SourceRef(location=raw)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the ``candidate-transform`` CLI.

    ``--input`` is required and repeatable (one entry per source reference);
    ``--config`` is the required projection config path; ``--output`` is optional and,
    when given, names the file to write the JSON output to.
    """
    parser = argparse.ArgumentParser(
        prog="candidate-transform",
        description=(
            "Transform candidate data from multiple sources into projected "
            "profiles emitted as JSON."
        ),
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        metavar="PATH_OR_URL",
        required=True,
        help=(
            "A source reference (file path or URL). Repeat for multiple sources. "
            "Optionally prefix with a source type, e.g. 'github=demo/profile.json' "
            "or 'linkedin=demo/profile.json', to force a specific adapter."
        ),
    )
    parser.add_argument(
        "--config",
        dest="configs",
        action="append",
        metavar="PATH",
        required=True,
        help=(
            "Path to a Projection_Config JSON document. Repeat to project the same "
            "canonical record into multiple schemas in a single run (the pipeline "
            "runs once; only projection repeats). With one --config the output is a "
            "JSON array of profiles; with several it is a JSON object keyed by "
            "config filename."
        ),
    )
    parser.add_argument(
        "--output",
        dest="output",
        metavar="PATH",
        default=None,
        help="Write JSON output to this file. When omitted, output goes to stdout.",
    )
    parser.add_argument(
        "--show-canonical",
        dest="show_canonical",
        action="store_true",
        help=(
            "Include the original canonical record (the full internal record, with "
            "provenance and confidence, that every projection is derived from) "
            "alongside the projected output. Demonstrates 'one canonical record -> "
            "many projections'."
        ),
    )
    return parser


def _load_config(path: str) -> ProjectionConfig:
    """Load and parse the projection config JSON at ``path`` into a config object.

    Raises :class:`ProjectionConfigError` when the file cannot be read, the JSON
    cannot be decoded, or the document is structurally invalid -- the CLI surfaces
    this as a configuration error naming the problem (Req 13.5).
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ProjectionConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ProjectionConfigError(f"could not read config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectionConfigError(
            f"config file {path} is not valid JSON: {exc}"
        ) from exc
    return ProjectionConfig.from_dict(data)


def _serialize(profiles: list[dict[str, Any]]) -> str:
    """Serialize the projected profiles into deterministic JSON text (Req 12.1).

    Keys are emitted in their existing insertion order (config order, preserved by
    the projection engine), so identical inputs yield byte-identical output.
    """
    return json.dumps(to_dict(profiles), ensure_ascii=False, indent=2)


def _banner(title: str) -> str:
    """Return a clear ASCII section banner for the human-readable showcase view."""
    rule = "=" * 64
    return f"{rule}\n  {title}\n{rule}"


def _render_showcase(
    canonical: Any, sections: list[tuple[str, Any]]
) -> str:
    """Render the canonical record and each projection as separate labelled blocks.

    Produces a readable, sectioned view (each block is valid JSON on its own) rather
    than one nested object, so the canonical record and the individual projections
    are visually distinct. Used by the ``--show-canonical`` demo view.
    """
    blocks: list[str] = [
        _banner("CANONICAL RECORD  (built once, before projection)"),
        json.dumps(canonical, ensure_ascii=False, indent=2),
    ]
    for label, payload in sections:
        blocks.append("")
        blocks.append(_banner(f"PROJECTION  ->  {label}"))
        blocks.append(json.dumps(payload, ensure_ascii=False, indent=2))
    return "\n".join(blocks)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the candidate-transform CLI and return a process exit code.

    Args:
        argv: Optional argument vector (excluding the program name). Defaults to
            ``sys.argv[1:]`` when ``None``.

    Returns:
        ``0`` on a clean run; ``2`` for a usage or configuration error; ``1`` when the
        run completed but produced source/projection errors.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed a usage error naming the missing/invalid argument
        # to stderr (Req 13.4); translate its exit into our return value.
        code = exc.code if isinstance(exc.code, int) else _EXIT_USAGE
        return code if code != 0 else _EXIT_USAGE

    # Load + parse each projection config; an unparseable or structurally invalid
    # config is a configuration error naming the problem (Req 13.5).
    configs = []
    for config_path in args.configs:
        try:
            configs.append(_load_config(config_path))
        except ProjectionConfigError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return _EXIT_CONFIG

    # Wire the inputs into the engine and run the pipeline (Req 13.1). The engine never
    # raises -- it always returns a RunResult.
    refs = [_parse_input_ref(location) for location in args.inputs]
    engine = TransformerEngine()

    if len(configs) == 1:
        result = engine.run(refs, configs[0])
        if args.show_canonical:
            # Demo view: canonical record then the single projection, as separate
            # labelled blocks.
            output_text = _render_showcase(
                to_dict(engine.canonicals),
                [(os.path.basename(args.configs[0]), to_dict(result.profiles))],
            )
        else:
            # Default shape: a JSON array of profiles.
            output_text = _serialize(result.profiles)
        errors = result.errors
        exit_code = result.exit_code
    else:
        # Multiple projections from ONE pipeline run: extraction, normalization, and
        # merge happen once; the shared canonical record is then projected into each
        # config.
        results = engine.run_multi(refs, configs)
        if args.show_canonical:
            # Demo view: the canonical record once, then each projection as its own
            # clearly-separated block -- "one canonical record -> many projections".
            output_text = _render_showcase(
                to_dict(engine.canonicals),
                [
                    (os.path.basename(path), to_dict(res.profiles))
                    for path, res in zip(args.configs, results)
                ],
            )
        else:
            # Machine shape: a JSON object keyed by config filename.
            projections = {
                os.path.basename(path): to_dict(res.profiles)
                for path, res in zip(args.configs, results)
            }
            output_text = json.dumps(projections, ensure_ascii=False, indent=2)
        # The ingest/extract/merge errors are shared across projections; dedupe so a
        # shared Error_Report is reported once.
        seen: set[tuple[Any, Any, Any]] = set()
        errors = []
        for res in results:
            for report in res.errors:
                key = (report.source, report.stage, report.error)
                if key not in seen:
                    seen.add(key)
                    errors.append(report)
        exit_code = max((res.exit_code for res in results), default=_EXIT_OK)

    if args.output is not None:
        # Write JSON to the requested file (Req 13.2).
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output_text)
            handle.write("\n")
    else:
        # Print JSON to standard output (Req 13.3).
        print(output_text)

    # Any source/projection error -> non-zero exit and the Error_Report entries are
    # printed to stderr (Req 13.7); a clean run exits 0 (Req 13.6).
    if errors:
        for report in errors:
            print(
                "error_report: "
                + json.dumps(to_dict(report), ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
        return _EXIT_RUN_ERRORS if exit_code == 0 else exit_code

    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

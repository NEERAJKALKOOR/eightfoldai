"""Multi-Source Candidate Data Transformer.

A deterministic, explainable pipeline that turns heterogeneous candidate inputs
into a single canonical candidate profile and projects it into any caller-specified
output schema at runtime.

This top-level package contains the engine library. The CLI lives in the
``candidate_transformer.cli`` subpackage and depends on the engine; the engine
must never import from the CLI.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

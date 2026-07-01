"""Engine orchestration package.

Houses the ``TransformerEngine`` orchestration (added in a later task) that wires
together adapters, normalizers, identity resolution, merge, projection, and
validation. This package must not import from ``candidate_transformer.cli``.
"""

from __future__ import annotations

from .confidence import (
    agreement_score,
    field_confidence,
    null_field_confidence,
    overall_confidence,
)
from .identity import (
    NAMESPACE_CANDIDATE,
    IdentityResolver,
    assign_candidate_ids,
    candidate_id_for_group,
    group,
)
from .merge import (
    LIST_VALUED_FIELDS,
    NOT_FOUND_METHOD,
    CandidateValue,
    ListContribution,
    ListFieldResult,
    combine_list_field,
    dedup_list_values,
    extract_list_items,
    list_contributions_for_field,
    list_value_sort_key,
    make_provenance,
    not_found_provenance,
    order_provenance,
    select_winner,
    winner_sort_key,
)
from .path_resolver import (
    MISSING,
    FieldSegment,
    IndexSegment,
    InvalidPathError,
    ProjectionSegment,
    Segment,
    parse_path,
    resolve_path,
)
from .projection import (
    FieldSpec,
    ProjectionConfig,
    ProjectionConfigError,
    ProjectionEngine,
    project,
)
from .validation import (
    ValidationError,
    Validator,
    validate,
)
from .transformer import (
    TransformerEngine,
    run,
)

__all__ = [
    "TransformerEngine",
    "run",
    "IdentityResolver",
    "group",
    "NAMESPACE_CANDIDATE",
    "candidate_id_for_group",
    "assign_candidate_ids",
    "agreement_score",
    "field_confidence",
    "null_field_confidence",
    "overall_confidence",
    "CandidateValue",
    "select_winner",
    "winner_sort_key",
    "LIST_VALUED_FIELDS",
    "NOT_FOUND_METHOD",
    "ListContribution",
    "ListFieldResult",
    "extract_list_items",
    "list_contributions_for_field",
    "list_value_sort_key",
    "dedup_list_values",
    "combine_list_field",
    "make_provenance",
    "not_found_provenance",
    "order_provenance",
    "MISSING",
    "InvalidPathError",
    "FieldSegment",
    "IndexSegment",
    "ProjectionSegment",
    "Segment",
    "parse_path",
    "resolve_path",
    "FieldSpec",
    "ProjectionConfig",
    "ProjectionConfigError",
    "ProjectionEngine",
    "project",
    "ValidationError",
    "Validator",
    "validate",
    "TransformerEngine",
    "run",
]

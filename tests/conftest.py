"""Shared pytest configuration and Hypothesis profiles.

Registers a ``ci`` Hypothesis settings profile with ``max_examples >= 100`` (as
required by the design's property-based testing strategy) and loads it by
default. Override at runtime with the ``HYPOTHESIS_PROFILE`` environment variable
(e.g. ``HYPOTHESIS_PROFILE=dev``).
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

# Default profile used for property-based tests. The design requires a minimum of
# 100 examples per property test.
settings.register_profile("ci", max_examples=100)

# A lighter profile for fast local iteration.
settings.register_profile("dev", max_examples=25)

# A thorough profile for deeper exploration when desired.
settings.register_profile(
    "thorough",
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))

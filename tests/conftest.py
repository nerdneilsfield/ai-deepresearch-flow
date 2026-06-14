from __future__ import annotations

import os

from hypothesis import HealthCheck, settings


settings.register_profile(
    "dev",
    max_examples=40,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci-fast",
    max_examples=80,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "nightly",
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci-fast"))

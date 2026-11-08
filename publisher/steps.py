"""The check and publish jobs as step functions (spec 006 R-3, plan "Check job" and "Publish job").

``run_check(job, deps)`` and ``run_publish(job, deps)`` take every collaborator through ``Deps`` so the
flows run against fakes. This module starts with the constants that must follow the Makefile.
"""
from __future__ import annotations

# The commands of the Makefile's ``check:`` recipe and its ``export`` variables (research R-6). The
# drift test (tests/publisher/test_makefile_drift.py) parses the Makefile and fails when they differ.
CHECK_STEPS: tuple[tuple[str, ...], ...] = (
    ("npm", "run", "build"),
    ("npm", "test"),
    ("npm", "run", "typecheck"),
    ("npm", "run", "check:dist"),
)
CHECK_ENV: dict[str, str] = {"ASTRO_TELEMETRY_DISABLED": "1"}

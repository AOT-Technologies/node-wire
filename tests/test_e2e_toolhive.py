# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""scripts/e2e_toolhive.py: the parallel build graph's ordering, and error-taxonomy parsing."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import e2e_toolhive as e2e  # noqa: E402


def _ancestors(jobs: list[e2e.Job]) -> dict[str, set[str]]:
    after = {job.name: set(job.after) for job in jobs}
    seen: dict[str, set[str]] = {}

    def walk(name: str) -> set[str]:
        if name not in seen:
            seen[name] = set()
            for dep in after[name]:
                seen[name] |= {dep} | walk(dep)
        return seen[name]

    for name in after:
        walk(name)
    return seen


def _ordered(ancestors: dict[str, set[str]], a: str, b: str) -> bool:
    return a in ancestors[b] or b in ancestors[a]


def _connector(job: e2e.Job) -> str:
    argv = list(job.argv)
    return argv[argv.index("--connector-id") + 1] if "--connector-id" in argv else ""


def _writes_wiring(job: e2e.Job) -> bool:
    return "gen-stacklok" in job.argv or ("gen-all" in job.argv and "--no-wire" not in job.argv)


def _builds_wheels(job: e2e.Job) -> bool:
    return "gen-stacklok" in job.argv or ("gen-all" in job.argv and "--no-wheel" not in job.argv)


def test_wiring_has_one_writer_at_a_time() -> None:
    jobs = e2e.build_jobs()
    ancestors = _ancestors(jobs)
    writers = [job.name for job in jobs if _writes_wiring(job)]
    assert len(writers) >= 3
    for a, b in itertools.combinations(writers, 2):
        assert _ordered(ancestors, a, b), f"{a} and {b} could wire at the same time"


def test_wheel_builds_that_share_packages_never_overlap() -> None:
    """Shared packages (runtime, bindings) and a connector's own Cython sources live in src/."""
    jobs = e2e.build_jobs()
    ancestors = _ancestors(jobs)
    builders = [job for job in jobs if _builds_wheels(job)]
    first = next(job for job in builders if "--no-mcp" in job.argv and "--no-wire" not in job.argv)
    stacklok = [job.name for job in builders if "gen-stacklok" in job.argv]
    for job in builders:
        if job is not first:
            assert first.name in ancestors[job.name], f"{job.name} may rebuild the shared wheels"
    for a, b in itertools.combinations(stacklok, 2):
        assert _ordered(ancestors, a, b), f"{a} and {b} may both build the Alpine wheels"
    for a, b in itertools.combinations(builders, 2):
        if _connector(a) == _connector(b):
            assert _ordered(ancestors, a.name, b.name), f"{a.name} and {b.name} overlap"


def test_gen_stacklok_waits_for_the_host_builds_of_its_connector() -> None:
    """It regenerates the connector, which the host's gen-mcp copies wheels from."""
    jobs = e2e.build_jobs()
    ancestors = _ancestors(jobs)
    for job in jobs:
        if "gen-stacklok" not in job.argv:
            continue
        for other in jobs:
            if "gen-mcp" in other.argv and _connector(other) == _connector(job):
                assert other.name in ancestors[job.name], f"{job.name} may race {other.name}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Error executing tool find_pets_by_status: TENANT_REQUIRED [AUTH]: X-Tenant-ID is "
            "required (trace_id=3b5d519f-7a6f-439b-937d-12615e0706e6)",
            e2e.Taxonomy("TENANT_REQUIRED", "AUTH", "3b5d519f-7a6f-439b-937d-12615e0706e6"),
        ),
        (
            "Error executing tool nw_select_config: CONFIG_NOT_FOUND [BUSINESS]: Unknown config",
            e2e.Taxonomy("CONFIG_NOT_FOUND", "BUSINESS", None),
        ),
        (
            json.dumps(
                {
                    "success": False,
                    "error_code": "SECRET_NOT_FOUND",
                    "error_category": "FATAL",
                    "trace_id": "t-1",
                }
            ),
            e2e.Taxonomy("SECRET_NOT_FOUND", "FATAL", "t-1"),
        ),
        (
            "Input validation error: $: 'channel' is a required property",
            e2e.Taxonomy(None, None, None),
        ),
    ],
)
def test_taxonomy_reads_stacklok_text_and_host_envelopes(text: str, expected: e2e.Taxonomy) -> None:
    assert e2e.taxonomy(text) == expected

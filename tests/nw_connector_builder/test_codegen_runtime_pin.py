# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated packages require a runtime that has the APIs their code calls.

A generated wheel pinned too low installs next to an older runtime and fails at
import (``RestEnvelopeError``) or silently mis-sends requests (``body_property``
fields ignored by an older ``split_params_by_location``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from nw_connector_builder.codegen import generate_package_pyproject

_REPO = Path(__file__).resolve().parents[2]


def _runtime_requirement() -> Requirement:
    project = tomllib.loads(generate_package_pyproject("demo"))["project"]
    (req,) = [Requirement(d) for d in project["dependencies"] if d.startswith("node-wire-runtime")]
    return req


def test_generated_package_requires_the_runtime_with_body_properties() -> None:
    assert not _runtime_requirement().specifier.contains("1.0.0")
    assert _runtime_requirement().specifier.contains("1.1.0")


def test_required_runtime_is_not_newer_than_the_runtime_in_this_repo() -> None:
    """Otherwise a freshly generated connector cannot install against the local runtime."""
    runtime = tomllib.loads((_REPO / "packages" / "runtime" / "pyproject.toml").read_text())
    assert _runtime_requirement().specifier.contains(Version(runtime["project"]["version"]))

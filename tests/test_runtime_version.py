#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for node_wire_runtime._resolve_version fallback branches."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import node_wire_runtime
from node_wire_runtime import _resolve_version


def test_resolve_version_from_installed_distribution() -> None:
    with patch("importlib.metadata.version", side_effect=["9.9.9"]) as pkg_version:
        assert _resolve_version() == "9.9.9"
    pkg_version.assert_called_once_with("node-wire-runtime")


def test_resolve_version_falls_back_to_pyproject() -> None:
    # Both distribution names unavailable -> read version from the src-layout
    # pyproject.toml on disk.
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
        version = _resolve_version()

    # Matches the version declared in the repository's root pyproject.toml.
    assert version.count(".") == 2
    assert all(part.isdigit() for part in version.split("."))


def test_resolve_version_returns_default_when_everything_fails() -> None:
    # Distributions missing and pyproject read raises -> hard-coded default.
    with (
        patch("importlib.metadata.version", side_effect=PackageNotFoundError),
        patch("pathlib.Path.read_text", side_effect=OSError("boom")),
    ):
        assert _resolve_version() == "0.0.0"


def test_module_exposes_version_string() -> None:
    assert isinstance(node_wire_runtime.__version__, str)
    assert node_wire_runtime.__version__

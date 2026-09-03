# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Resolve the node-wire repo root (no ``--node-wire-root`` flag)."""

from __future__ import annotations

from pathlib import Path


class RootError(Exception):
    """Cannot locate a valid node-wire repo root."""


def _looks_like_node_wire(path: Path) -> bool:
    return (
        (path / "pyproject.toml").is_file()
        and (path / "scripts" / "build-packages.sh").is_file()
        and (path / "config" / "connectors.yaml").is_file()
    )


def _package_dir() -> Path:
    # src/nw_cli/root.py → nw-cli/
    return Path(__file__).resolve().parents[2]


def resolve_node_wire_root() -> Path:
    """Return the node-wire repo root.

    Prefers ``Path.cwd()`` when it looks like node-wire; otherwise falls back
    to the parent of the ``nw-cli`` package (covers ``uv run --directory nw-cli``).
    """
    cwd = Path.cwd().resolve()
    if _looks_like_node_wire(cwd):
        return cwd

    parent = _package_dir().parent
    if _looks_like_node_wire(parent):
        return parent

    raise RootError(
        "Run `nw` from the node-wire repo root "
        "(expected scripts/build-packages.sh and config/connectors.yaml)."
    )

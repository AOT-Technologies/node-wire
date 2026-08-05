# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for node-wire root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from nw_cli.root import RootError, resolve_node_wire_root


def test_resolve_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "connectors.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build-packages.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_node_wire_root() == tmp_path.resolve()


def test_resolve_fails_without_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # Package parent may still be a real node-wire checkout when tests run from
    # the monorepo — only assert failure when neither cwd nor package parent match.
    # Use an isolated fake package dir via patch.
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        mp.setattr("nw_cli.root._package_dir", lambda: tmp_path / "nw-cli")
        (tmp_path / "nw-cli").mkdir()
        with pytest.raises(RootError, match="repo root"):
            resolve_node_wire_root()

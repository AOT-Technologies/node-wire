# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for atomic promote / rollback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nw_connector_builder.promote import PromoteError, promote


def _staging_tree(staging: Path, connector_id: str) -> None:
    src = staging / "src" / f"node_wire_{connector_id}"
    pkg = staging / "packages" / "connectors" / connector_id
    src.mkdir(parents=True)
    pkg.mkdir(parents=True)
    (src / "logic.py").write_text("# staged\n", encoding="utf-8")
    (pkg / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def test_promote_requires_force_when_dest_exists(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    root = tmp_path / "root"
    _staging_tree(staging, "pet_store")
    (root / "src" / "node_wire_pet_store").mkdir(parents=True)
    (root / "src" / "node_wire_pet_store" / "old.py").write_text("old\n", encoding="utf-8")

    with pytest.raises(PromoteError, match="--force"):
        promote(staging, root, "pet_store", force=False)


def test_promote_happy_path(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    root = tmp_path / "root"
    _staging_tree(staging, "pet_store")

    promote(staging, root, "pet_store", force=False)

    assert (root / "src" / "node_wire_pet_store" / "logic.py").read_text(encoding="utf-8") == (
        "# staged\n"
    )
    assert (root / "packages" / "connectors" / "pet_store" / "pyproject.toml").is_file()
    assert not (root / "src" / "node_wire_pet_store.promoting").exists()
    assert not (root / "src" / "node_wire_pet_store.bak").exists()


def test_promote_force_overwrites_existing(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    root = tmp_path / "root"
    _staging_tree(staging, "pet_store")
    dest = root / "src" / "node_wire_pet_store"
    dest.mkdir(parents=True)
    (dest / "old.py").write_text("old\n", encoding="utf-8")
    (root / "packages" / "connectors" / "pet_store").mkdir(parents=True)
    (root / "packages" / "connectors" / "pet_store" / "old.toml").write_text(
        "x\n", encoding="utf-8"
    )

    promote(staging, root, "pet_store", force=True)

    assert not (dest / "old.py").exists()
    assert (dest / "logic.py").is_file()
    assert not (root / "packages" / "connectors" / "pet_store" / "old.toml").exists()


def test_promote_force_keeps_built_wheels(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    root = tmp_path / "root"
    _staging_tree(staging, "pet_store")
    dist = root / "packages" / "connectors" / "pet_store" / "dist"
    dist.mkdir(parents=True)
    (dist / "pet_store-1.0-cp313-cp313-musllinux_1_2_aarch64.whl").write_bytes(b"")
    (dist / ".nw-source-cp313-musllinux.sha256").write_text("abc\n", encoding="utf-8")

    promote(staging, root, "pet_store", force=True)

    assert sorted(p.name for p in dist.iterdir()) == [
        ".nw-source-cp313-musllinux.sha256",
        "pet_store-1.0-cp313-cp313-musllinux_1_2_aarch64.whl",
    ]
    assert (dist.parent / "pyproject.toml").is_file()
    assert not (dist.parent.with_name("pet_store.bak")).exists()


def test_promote_rollback_when_second_rename_fails(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    root = tmp_path / "root"
    _staging_tree(staging, "pet_store")
    dest_src = root / "src" / "node_wire_pet_store"
    dest_pkg = root / "packages" / "connectors" / "pet_store"
    dest_src.mkdir(parents=True)
    dest_pkg.mkdir(parents=True)
    (dest_src / "kept.py").write_text("keep-src\n", encoding="utf-8")
    (dest_pkg / "kept.toml").write_text("keep-pkg\n", encoding="utf-8")

    real_rename = Path.rename
    call_count = {"n": 0}

    def flaky_rename(self: Path, target: Path) -> Path:  # type: ignore[override]
        # Skip backup renames; fail on the second promoting→final rename (pkg).
        if self.name.endswith(".promoting"):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated pkg rename failure")
        return real_rename(self, target)

    with patch.object(Path, "rename", flaky_rename):
        with pytest.raises(OSError, match="simulated"):
            promote(staging, root, "pet_store", force=True)

    assert (dest_src / "kept.py").read_text(encoding="utf-8") == "keep-src\n"
    assert (dest_pkg / "kept.toml").read_text(encoding="utf-8") == "keep-pkg\n"
    assert not (root / "src" / "node_wire_pet_store.promoting").exists()
    assert not (root / "packages" / "connectors" / "pet_store.promoting").exists()


def test_promote_leaves_build_caches_behind(tmp_path: Path) -> None:
    """The gate imports and pytests staging in place; those caches are not output."""
    staging = tmp_path / "staging"
    src_stage = staging / "src" / "node_wire_demo"
    pkg_stage = staging / "packages" / "connectors" / "demo" / "tests"
    src_stage.mkdir(parents=True)
    pkg_stage.mkdir(parents=True)
    (src_stage / "logic.py").write_text("x = 1\n", encoding="utf-8")
    (src_stage / "__pycache__").mkdir()
    (src_stage / "__pycache__" / "logic.cpython-312.pyc").write_bytes(b"\x00")
    (pkg_stage / "test_demo_models.py").write_text("def test_x() -> None: ...\n", encoding="utf-8")
    (pkg_stage.parent / ".pytest_cache").mkdir()
    (pkg_stage.parent / ".pytest_cache" / "CACHEDIR.TAG").write_text("x", encoding="utf-8")

    root = tmp_path / "root"
    promote(staging, root, "demo", force=False)

    src_dest = root / "src" / "node_wire_demo"
    pkg_dest = root / "packages" / "connectors" / "demo"
    assert (src_dest / "logic.py").is_file()
    assert (pkg_dest / "tests" / "test_demo_models.py").is_file()
    assert not (src_dest / "__pycache__").exists()
    assert not (pkg_dest / ".pytest_cache").exists()


def _staged(tmp_path: Path, connector_id: str) -> Path:
    staging = tmp_path / "staging"
    src = staging / "src" / f"node_wire_{connector_id}"
    pkg = staging / "packages" / "connectors" / connector_id
    src.mkdir(parents=True)
    pkg.mkdir(parents=True)
    (src / "logic.py").write_text(
        "# Generated by nw-connector-builder — do not hand-edit.\nx = 1\n", encoding="utf-8"
    )
    (pkg / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return staging


def test_promote_refuses_to_overwrite_a_hand_written_connector(tmp_path: Path) -> None:
    """--force means 'replace what I generated', not 'delete the hand-written slack'."""
    staging = _staged(tmp_path, "slack")
    root = tmp_path / "root"
    hand_written = root / "src" / "node_wire_slack"
    hand_written.mkdir(parents=True)
    (hand_written / "logic.py").write_text("class SlackConnector: ...\n", encoding="utf-8")

    with pytest.raises(PromoteError, match="not written by nw-connector-builder"):
        promote(staging, root, "slack", force=True)

    # The hand-written source is untouched — no partial replacement.
    assert (hand_written / "logic.py").read_text() == "class SlackConnector: ...\n"


def test_promote_still_replaces_its_own_output(tmp_path: Path) -> None:
    staging = _staged(tmp_path, "demo")
    root = tmp_path / "root"
    previous = root / "src" / "node_wire_demo"
    previous.mkdir(parents=True)
    (previous / "logic.py").write_text(
        "# Generated by nw-connector-builder — do not hand-edit.\nold = True\n", encoding="utf-8"
    )

    promote(staging, root, "demo", force=True)
    assert "x = 1" in (previous / "logic.py").read_text()

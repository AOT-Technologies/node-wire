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
    (root / "packages" / "connectors" / "pet_store" / "old.toml").write_text("x\n", encoding="utf-8")

    promote(staging, root, "pet_store", force=True)

    assert not (dest / "old.py").exists()
    assert (dest / "logic.py").is_file()
    assert not (root / "packages" / "connectors" / "pet_store" / "old.toml").exists()


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

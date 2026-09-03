# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Atomic staging → repo promote."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class PromoteError(Exception):
    pass


def _prepare_promoting(src: Path, dest: Path) -> Path:
    """Copy ``src`` to ``dest.promoting`` (overwriting any stale sibling)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    promoting = dest.with_name(dest.name + ".promoting")
    if promoting.exists():
        shutil.rmtree(promoting)
    shutil.copytree(src, promoting)
    return promoting


def _backup_if_exists(dest: Path) -> Path | None:
    if not dest.exists():
        return None
    backup = dest.with_name(dest.name + ".bak")
    if backup.exists():
        shutil.rmtree(backup)
    dest.rename(backup)
    return backup


def _restore_backup(backup: Path | None, dest: Path) -> None:
    if backup is None or not backup.exists():
        return
    if dest.exists():
        shutil.rmtree(dest)
    backup.rename(dest)


def _cleanup_backup(backup: Path | None) -> None:
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def promote(
    staging: Path,
    node_wire_root: Path,
    connector_id: str,
    *,
    force: bool,
) -> None:
    """Promote both trees with a two-phase commit and cross-tree rollback.

    1. Copy both staging trees to ``*.promoting`` siblings (no dest mutation yet).
    2. Move existing destinations aside to ``*.bak`` (if any).
    3. Rename both ``*.promoting`` → final destinations.
    4. On failure after step 2/3 starts, restore both backups and discard promoting.
    """
    src_dest = node_wire_root / "src" / f"node_wire_{connector_id}"
    pkg_dest = node_wire_root / "packages" / "connectors" / connector_id
    src_stage = staging / "src" / f"node_wire_{connector_id}"
    pkg_stage = staging / "packages" / "connectors" / connector_id

    if (src_dest.exists() or pkg_dest.exists()) and not force:
        raise PromoteError(f"Destination already exists for {connector_id!r}; re-run with --force")

    src_promoting = _prepare_promoting(src_stage, src_dest)
    pkg_promoting = _prepare_promoting(pkg_stage, pkg_dest)

    # (backup_path | None, destination) — built incrementally so a mid-backup
    # failure still restores whatever was already moved aside.
    backups: list[tuple[Path | None, Path]] = []
    try:
        backups.append((_backup_if_exists(src_dest), src_dest))
        backups.append((_backup_if_exists(pkg_dest), pkg_dest))

        src_promoting.rename(src_dest)
        try:
            pkg_promoting.rename(pkg_dest)
        except Exception:
            # Undo src rename; outer handler restores *.bak trees.
            if src_dest.exists():
                shutil.rmtree(src_dest)
            raise

        for backup, _ in backups:
            _cleanup_backup(backup)
    except Exception:
        if src_promoting.exists():
            shutil.rmtree(src_promoting, ignore_errors=True)
        if pkg_promoting.exists():
            shutil.rmtree(pkg_promoting, ignore_errors=True)
        # After successful cleanup above, backup dirs are already gone.
        for backup, dest in backups:
            _restore_backup(backup, dest)
        raise

    logger.info("Promoted connector %s", connector_id)

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
    if dest.exists():
        shutil.rmtree(dest)
    if backup is not None and backup.exists():
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
        raise PromoteError(
            f"Destination already exists for {connector_id!r}; re-run with --force"
        )

    src_promoting = _prepare_promoting(src_stage, src_dest)
    pkg_promoting = _prepare_promoting(pkg_stage, pkg_dest)

    src_backup: Path | None = None
    pkg_backup: Path | None = None
    try:
        src_backup = _backup_if_exists(src_dest)
        pkg_backup = _backup_if_exists(pkg_dest)

        src_promoting.rename(src_dest)
        try:
            pkg_promoting.rename(pkg_dest)
        except Exception:
            # Roll back src promote; restore both backups.
            if src_dest.exists():
                shutil.rmtree(src_dest)
            _restore_backup(src_backup, src_dest)
            _restore_backup(pkg_backup, pkg_dest)
            src_backup = None
            pkg_backup = None
            raise

        _cleanup_backup(src_backup)
        _cleanup_backup(pkg_backup)
        src_backup = None
        pkg_backup = None
    except Exception:
        # Ensure promoting leftovers are cleaned up; backups restored above on
        # the inner failure path. Outer failures before rename restore nothing.
        if src_promoting.exists():
            shutil.rmtree(src_promoting, ignore_errors=True)
        if pkg_promoting.exists():
            shutil.rmtree(pkg_promoting, ignore_errors=True)
        if src_backup is not None or pkg_backup is not None:
            _restore_backup(src_backup, src_dest)
            _restore_backup(pkg_backup, pkg_dest)
        raise

    logger.info("Promoted connector %s", connector_id)

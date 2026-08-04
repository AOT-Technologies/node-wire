# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Atomic staging → repo promote."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class PromoteError(Exception):
    pass


def _replace_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    promoting = dest.with_name(dest.name + ".promoting")
    if promoting.exists():
        shutil.rmtree(promoting)
    shutil.copytree(src, promoting)
    if dest.exists():
        backup = dest.with_name(dest.name + ".bak")
        if backup.exists():
            shutil.rmtree(backup)
        dest.rename(backup)
        try:
            promoting.rename(dest)
        except Exception:
            backup.rename(dest)
            raise
        shutil.rmtree(backup)
    else:
        promoting.rename(dest)


def promote(
    staging: Path,
    node_wire_root: Path,
    connector_id: str,
    *,
    force: bool,
) -> None:
    src_dest = node_wire_root / "src" / f"node_wire_{connector_id}"
    pkg_dest = node_wire_root / "packages" / "connectors" / connector_id
    src_stage = staging / "src" / f"node_wire_{connector_id}"
    pkg_stage = staging / "packages" / "connectors" / connector_id

    if (src_dest.exists() or pkg_dest.exists()) and not force:
        raise PromoteError(
            f"Destination already exists for {connector_id!r}; re-run with --force"
        )

    _replace_tree(src_stage, src_dest)
    _replace_tree(pkg_stage, pkg_dest)
    logger.info("Promoted connector %s", connector_id)

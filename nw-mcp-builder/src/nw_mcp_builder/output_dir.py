# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generate a project directory all-or-nothing.

A generator writes into a staging folder next to the final one; only a complete project is
renamed into place, and the project it replaces is removed only after that. A failed or
interrupted run leaves the previous project as it was, never a partial one. Shared by the
nw-mcp-builder host generator and ``nw gen-stacklok``.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_STAGING = ".tmp-"
_OLD = ".old-"


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # exists, owned by someone else (or cannot tell): leave it alone
    return True


def clean_stale_staging(final: Path) -> None:
    """Remove staging / replaced folders for ``final`` left by runs that no longer exist."""
    parent = final.parent
    if not parent.is_dir():
        return
    for marker in (_STAGING, _OLD):
        prefix = f".{final.name}{marker}"
        for leftover in parent.glob(f"{prefix}*"):
            pid = leftover.name[len(prefix) :]
            if pid.isdigit() and _pid_alive(int(pid)):
                continue
            logger.info("Removing leftover %s from an interrupted run", leftover)
            shutil.rmtree(leftover, ignore_errors=True)


@contextmanager
def atomic_output_dir(final: Path, *, replace: bool) -> Iterator[Path]:
    """Yield a staging *parent* folder; the generator writes ``<staging>/<final.name>``.

    On a clean exit that folder replaces ``final`` (``replace`` must allow it when ``final``
    exists, which is checked before anything is written). On an exception the staging folder is
    removed and ``final`` is untouched.
    """
    clean_stale_staging(final)
    if final.exists() and not replace:
        raise FileExistsError(f"Output project already exists: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.parent / f".{final.name}{_STAGING}{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        yield staging
        built = staging / final.name
        if not built.is_dir():
            raise FileNotFoundError(f"The generator wrote no project at {built}")
        old = final.parent / f".{final.name}{_OLD}{os.getpid()}"
        if final.exists():
            final.rename(old)
        try:
            built.rename(final)
        except BaseException:
            if old.exists() and not final.exists():
                old.rename(final)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

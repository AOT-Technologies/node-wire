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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STAGING = ".tmp-"
_OLD = ".old-"


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # Not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, which interrupts the
        # process group instead of probing, and a missing pid raises a plain OSError.
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # exists, owned by someone else (or cannot tell): leave it alone
    return True


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def _pid_alive_windows(
    pid: int, kernel32: Any = None, get_last_error: Callable[[], int] | None = None
) -> bool:
    """Whether ``pid`` is a running process, asked of the Win32 API without touching it.

    ``kernel32`` / ``get_last_error`` default to the real ones; tests pass fakes.
    """
    import ctypes

    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    if get_last_error is None:
        get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied: it exists, owned by someone else. Anything else (87, invalid
        # parameter): no such process.
        return get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # cannot tell: leave it alone
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Leftover staging folders are kept only while the run that made them is alive, on every OS."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from nw_mcp_builder import output_dir
from nw_mcp_builder.output_dir import _pid_alive_windows, clean_stale_staging

_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_ERROR_ACCESS_DENIED = 5


class _FakeKernel32:
    """``OpenProcess`` / ``GetExitCodeProcess`` for one process (or none)."""

    def __init__(self, *, exit_code: Optional[int] = None) -> None:
        self.exit_code = exit_code  # None: OpenProcess fails
        self.closed = False

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:  # noqa: N802
        return 0 if self.exit_code is None else 42

    def GetExitCodeProcess(self, handle: int, ref) -> int:  # noqa: N802
        ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        self.closed = True
        return 1


def test_a_missing_process_is_not_alive() -> None:
    assert not _pid_alive_windows(
        999999999, _FakeKernel32(), get_last_error=lambda: _ERROR_INVALID_PARAMETER
    )


def test_another_users_process_is_alive() -> None:
    assert _pid_alive_windows(4, _FakeKernel32(), get_last_error=lambda: _ERROR_ACCESS_DENIED)


def test_a_running_process_is_alive_and_its_handle_closed() -> None:
    kernel32 = _FakeKernel32(exit_code=_STILL_ACTIVE)
    assert _pid_alive_windows(1234, kernel32, get_last_error=lambda: 0)
    assert kernel32.closed


def test_an_exited_process_is_not_alive() -> None:
    kernel32 = _FakeKernel32(exit_code=0)
    assert not _pid_alive_windows(1234, kernel32, get_last_error=lambda: 0)
    assert kernel32.closed


def test_windows_never_signals_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows ``os.kill(pid, 0)`` sends CTRL_C_EVENT; the cleanup must not call it."""
    final = tmp_path / "demo-nw-mcp"
    leftover = tmp_path / ".demo-nw-mcp.tmp-999999999"
    leftover.mkdir()

    def no_kill(pid: int, sig: int) -> None:
        raise AssertionError("os.kill called on Windows")

    monkeypatch.setattr(output_dir.os, "name", "nt")
    monkeypatch.setattr(output_dir.os, "kill", no_kill)
    monkeypatch.setattr(output_dir, "_pid_alive_windows", lambda pid: False)

    clean_stale_staging(final)

    assert not leftover.exists()

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the nw-cli tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nw_cli import prerequisites, ui


@pytest.fixture(autouse=True)
def _docker_available(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Wheel and image builds check Docker first; the tests mock the builds themselves."""
    prerequisites.docker_problem.cache_clear()
    monkeypatch.setattr(prerequisites, "_probe_docker", lambda: None)
    yield
    prerequisites.docker_problem.cache_clear()


@pytest.fixture(autouse=True)
def _default_run_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global --verbose / --debug flags are process state; start each test from defaults."""
    monkeypatch.setattr(ui, "options", ui.RunOptions())


@pytest.fixture(autouse=True)
def _log_files_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """Per-run log files go to the test's temp dir, not the real <tmp>/nw-logs/."""
    folder = tmp_path_factory.mktemp("nw-logs")
    monkeypatch.setattr(ui, "new_log_file", lambda command: folder / f"{command}.log")

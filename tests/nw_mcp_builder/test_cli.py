#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""CLI tests for nw-mcp-builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nw_mcp_builder import cli


def test_cli_requires_connector_id() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_cli_accepts_short_and_long_connector_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "out" / "demo-mcp"
    project.mkdir(parents=True)

    with patch.object(cli, "run_from_connector", return_value=project) as run:
        cli.main(["-c", "google_drive", "--skip-build-wheels"])
        run.assert_called_once()
        assert run.call_args.args[0] == "google_drive"
        assert run.call_args.kwargs["skip_build_wheels"] is True

    out = capsys.readouterr().out
    assert "google_drive" in out
    assert str(project) in out

    with patch.object(cli, "run_from_connector", return_value=project) as run:
        cli.main(["--connector-id", "salesforce", "--force-output", "-v"])
        assert run.call_args.args[0] == "salesforce"
        assert run.call_args.kwargs["force_output"] is True


def test_cli_exits_one_on_run_error(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(cli, "run_from_connector", side_effect=FileNotFoundError("missing wheels")):
        with pytest.raises(SystemExit) as exc:
            cli.main(["-c", "google_drive"])
        assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "missing wheels" in err


def test_cli_forwards_paths_and_python(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    nw_root = tmp_path / "nw"
    out = tmp_path / "out"
    fixtures = tmp_path / "fixtures"

    with patch.object(cli, "run_from_connector", return_value=project) as run:
        cli.main(
            [
                "-c",
                "slack",
                "--node-wire-root",
                str(nw_root),
                "-o",
                str(out),
                "--fixtures-dir",
                str(fixtures),
                "--python",
                "3.12",
                "--force-fixture",
            ]
        )
        kwargs = run.call_args.kwargs
        assert kwargs["node_wire_root"] == nw_root.resolve()
        assert kwargs["output_dir"] == out
        assert kwargs["fixtures_dir"] == fixtures
        assert kwargs["python"] == "3.12"
        assert kwargs["force_fixture"] is True

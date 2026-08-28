#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""CLI tests for nw-connector-builder (subcommands + legacy flat flags)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nw_connector_builder import cli


def test_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "from-openapi" in out
    assert "mcp" in out


def test_from_openapi_subcommand_forwards_to_run_build() -> None:
    with patch.object(cli, "run_build", return_value=0) as run:
        with pytest.raises(SystemExit) as exc:
            cli.main(
                [
                    "from-openapi",
                    "--path",
                    "spec.yaml",
                    "--id",
                    "pet_store",
                    "--no-mcp",
                    "--force",
                ]
            )
        assert exc.value.code == 0
        kwargs = run.call_args.kwargs
        assert kwargs["spec"] == "spec.yaml"
        assert kwargs["connector_id"] == "pet_store"
        assert kwargs["no_mcp"] is True
        assert kwargs["force"] is True


def test_legacy_flat_flags_map_to_from_openapi() -> None:
    with patch.object(cli, "run_build", return_value=0) as run:
        with pytest.raises(SystemExit) as exc:
            cli.main(["--path", "https://example.com/openapi.json", "--id", "my_api"])
        assert exc.value.code == 0
        assert run.call_args.kwargs["connector_id"] == "my_api"


def test_from_openapi_rejects_invalid_id(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["from-openapi", "--path", "spec.yaml", "--id", "Bad-Id"])
    assert exc.value.code == 2
    assert "invalid --id" in capsys.readouterr().err


def test_mcp_subcommand_delegates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "out" / "demo-mcp"
    project.mkdir(parents=True)

    with patch("nw_mcp_builder.cli.run_from_connector", return_value=project) as run:
        cli.main(["mcp", "-c", "google_drive", "--skip-build-wheels", "--force-output"])
        run.assert_called_once()
        assert run.call_args.args[0] == "google_drive"
        assert run.call_args.kwargs["skip_build_wheels"] is True
        assert run.call_args.kwargs["force_output"] is True

    out = capsys.readouterr().out
    assert "google_drive" in out
    assert str(project) in out


def test_mcp_subcommand_exits_one_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "nw_mcp_builder.cli.run_from_connector",
        side_effect=FileNotFoundError("missing wheels"),
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(["mcp", "-c", "google_drive"])
        assert exc.value.code == 1
    assert "missing wheels" in capsys.readouterr().err

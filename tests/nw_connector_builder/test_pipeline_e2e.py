# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from nw_connector_builder.pipeline import BuildError, UsageError, run_build

FIXTURES = Path(__file__).parent / "fixtures"


def _mini_root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    (root / "src").mkdir(parents=True)
    (root / "packages" / "connectors").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "nw-mcp-builder").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")
    return root


def test_e2e_no_mcp_promote(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    code = run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
        wire=True,
        force=False,
        report_path=tmp_path / "abort.json",
    )
    assert code == 0
    assert (root / "src" / "node_wire_demo_pets" / "logic.py").is_file()
    assert (root / "packages" / "connectors" / "demo_pets" / "pyproject.toml").is_file()
    assert (root / "packages" / "connectors" / "demo_pets" / "report.json").is_file()
    yaml_text = (root / "config" / "connectors.yaml").read_text()
    assert "demo_pets" in yaml_text
    env_text = (root / "sample.env").read_text()
    assert "demo_pets" in env_text
    assert "DEMO_PETS_API_KEY" in env_text


def test_force_required_on_second_build(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
    )
    with pytest.raises(UsageError, match="--force"):
        run_build(
            spec=str(FIXTURES / "demo_pets.openapi.yaml"),
            connector_id="demo_pets",
            node_wire_root=root,
            no_mcp=True,
            force=False,
        )
    code = run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
        force=True,
    )
    assert code == 0


def test_remote_ref_leaves_repo_untouched(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "openapi: 3.0.3\ninfo: {title: t, version: '1'}\n"
        "servers: [{url: 'https://api.example.com'}]\n"
        "paths:\n  /x:\n    get:\n      responses:\n"
        "        '200':\n          description: ok\n"
        "          content:\n            application/json:\n"
        "              schema:\n                $ref: https://evil.example/schema.json\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        run_build(
            spec=str(bad),
            connector_id="evil_api",
            node_wire_root=root,
            no_mcp=True,
            report_path=tmp_path / "report.json",
        )
    assert not (root / "src" / "node_wire_evil_api").exists()

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Pristine parity: the vendored stacklok generator, given a scope without ``runtime:``,
produces the stock stacklok project (httpx client, ToolHive MCPServer manifest)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_builder.pipeline import run_pipeline

from .conftest import FIXTURES, TEMPLATE


def _compile_all(project: Path) -> None:
    for path in project.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


@pytest.mark.parametrize(
    ("scope", "spec", "server", "tools", "manifests"),
    [
        (
            "minimal_api.yaml",
            "minimal_api_openapi.yaml",
            "minimal-api",
            ["get_status"],
            {"mcpserver.yaml", "README.md"},
        ),
        (
            "weather_api.yaml",
            "weather_api_openapi.yaml",
            "weather-api",
            None,
            {"mcpserver.yaml", "mcpexternalauthconfig.yaml", "secret.yaml", "README.md"},
        ),
        (
            "petstore.yaml",
            "petstore_openapi.json",
            "petstore",
            [
                "find_pets_by_status",
                "get_pet_by_id",
                "place_order",
                "get_order_by_id",
                "get_user_by_name",
            ],
            {"mcpserver.yaml", "mcpexternalauthconfig.yaml", "secret.yaml", "README.md"},
        ),
    ],
)
def test_stock_generation(
    tmp_path: Path,
    scope: str,
    spec: str,
    server: str,
    tools: list[str] | None,
    manifests: set[str],
) -> None:
    project = run_pipeline(FIXTURES / scope, FIXTURES / spec, TEMPLATE, tmp_path)

    assert project == tmp_path / f"{server}-mcp"
    module = project / "src" / (server.replace("-", "_") + "_mcp")
    client = (module / "client.py").read_text(encoding="utf-8")
    assert "import httpx" in client
    tools_src = (module / "api" / "tools.py").read_text(encoding="utf-8")
    assert "self._client.request" in tools_src
    for tool in tools or []:
        assert f"async def {tool}(" in tools_src
    assert not (module / "api" / "models.py").exists()
    assert {p.name for p in (project / "deploy").iterdir()} == manifests
    assert "kind: MCPServer" in (project / "deploy" / "mcpserver.yaml").read_text()
    assert f'name = "{server}-mcp"' in (project / "pyproject.toml").read_text()
    _compile_all(project)

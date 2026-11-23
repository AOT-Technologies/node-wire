#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Action exposure, end to end, with the real GitHub connector (1,239 actions).

An author writes profiles with the real ``nw expose`` CLI in a throwaway node-wire workspace,
then the MCP, REST and gRPC hosts start with ``NW_EXPOSE_PROFILE`` and find the profile through
the default ``expose/`` folder beside ``config/``. Nothing inside the process is replaced except
the outbound HTTP client: a fake GitHub answers calls and records every request, so a test can
show that a hidden action never reached the upstream API. No real token is used.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock

import httpx
import mcp.types as types
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import bindings.mcp_server.server as mcp_server
import bindings.rest_api.app as rest_app
from bindings.grpc_server import connector_pb2
from bindings.grpc_server.server import ConnectorServiceServicer
from node_wire_runtime.exposure import ExposureConfigError
from nw_cli.cli import app as nw

REPO = Path(__file__).resolve().parents[1]
TOTAL = 1239  # actions on the generated GitHub connector

# The plan's two example profiles, as `nw expose new` arguments.
BRANCHES = [
    "-s", "path:/repos/{owner}/{repo}/branches/**",
    "-s", "path:/repos/{owner}/{repo}/git/ref/**",
    "-s", "path:/repos/{owner}/{repo}/git/refs/**",
    "-s", "path:/repos/{owner}/{repo}/git/matching-refs/**",
    "-s", "pulls_update_branch",
    "-x", "path:/repos/{owner}/{repo}/branches/*/protection/**",
]  # fmt: skip
PRS = [
    "-s", "path:/repos/{owner}/{repo}/pulls/**",
    "-s", "path:/repos/{owner}/{repo}/stacks/**",
    "-x", "pulls_merge*",
    "-x", "codespaces_*",
]  # fmt: skip
BRANCH_ACTIONS = [
    "git_create_ref",
    "git_delete_ref",
    "git_get_ref",
    "git_list_matching_refs",
    "git_update_ref",
    "pulls_update_branch",
    "repos_get_branch",
    "repos_list_branches",
    "repos_rename_branch",
]

runner = CliRunner()


# --------------------------------------------------------------------------- workspace


@pytest.fixture(scope="module")
def _workspace_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One copy of the GitHub connector source per module (copying is the slow part)."""
    src = tmp_path_factory.mktemp("nw-src") / "src"
    shutil.copytree(
        REPO / "src" / "node_wire_github",
        src / "node_wire_github",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return src


@pytest.fixture
def workspace(
    tmp_path: Path, _workspace_src: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """A node-wire checkout `nw` accepts, with an empty ``expose/`` and the real GitHub config."""
    root = tmp_path / "node-wire"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "build-packages.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'node-wire'\n", encoding="utf-8")
    (root / "config").mkdir()
    shutil.copy2(REPO / "config" / "connectors.yaml", root / "config" / "connectors.yaml")
    shutil.copytree(_workspace_src, root / "src")

    monkeypatch.chdir(root)
    monkeypatch.setenv("NW_CONFIG_PATH", str(root / "config" / "connectors.yaml"))
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "github")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-real")
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")
    monkeypatch.delenv("NW_EXPOSE_PROFILE", raising=False)
    monkeypatch.delenv("NW_EXPOSE_DIR", raising=False)

    # `nw expose` re-imports node_wire_github from the workspace; put the original back after.
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] == "node_wire_github"}
    yield root
    for key in [k for k in sys.modules if k.split(".")[0] == "node_wire_github"]:
        del sys.modules[key]
    sys.modules.update(saved)


def expose(*args: str) -> Any:
    return runner.invoke(nw, ["expose", *args])


def profile(root: Path, name: str) -> dict[str, Any]:
    return yaml.safe_load((root / "expose" / "github" / f"{name}.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- authoring


def test_author_creates_the_branches_profile_from_path_selectors(workspace: Path) -> None:
    result = expose("new", "github", "github-branches", *BRANCHES)

    assert result.exit_code == 0, result.output
    assert f"9 of {TOTAL} actions" in result.output
    doc = profile(workspace, "github-branches")
    assert doc["actions"] == BRANCH_ACTIONS
    assert doc["connector"] == "github" and doc["profile"] == "github-branches"
    assert doc["exclude"] == ["path:/repos/{owner}/{repo}/branches/*/protection/**"]
    assert "pulls_update_branch" in doc["select"]


def test_author_creates_the_prs_profile_with_stacks_and_without_merges(workspace: Path) -> None:
    result = expose("new", "github", "github-prs", *PRS)

    assert result.exit_code == 0, result.output
    actions = profile(workspace, "github-prs")["actions"]
    assert len(actions) == 36
    assert len([a for a in actions if a.startswith("pull_request_stacks_")]) == 5
    assert len([a for a in actions if a.startswith("reactions_")]) == 3
    assert "pulls_get" in actions and "pulls_create" in actions
    assert not [a for a in actions if a.startswith(("pulls_merge", "codespaces_"))]


@pytest.mark.parametrize(
    ("selectors", "message"),
    [
        (["-s", "tag:repos"], "needs action tags"),  # GitHub's actions carry no OpenAPI tags
        (["-s", "method:GET", "-x", "repos_delete_brnach"], "matches no action"),  # typo
        (["-s", "search:list branches"], "Bad selector"),  # ranking never decides a profile
    ],
    ids=["tag-without-metadata", "misspelled-exclude", "search-selector"],
)
def test_author_is_refused_a_profile_that_would_not_mean_what_it_says(
    workspace: Path, selectors: list[str], message: str
) -> None:
    result = expose("new", "github", "refused", *selectors)

    assert result.exit_code != 0
    assert message in result.output
    assert "bug in nw" not in result.output  # a user mistake, not a crash
    assert not (workspace / "expose" / "github" / "refused.yaml").exists()


def test_an_existing_profile_is_kept_unless_forced(workspace: Path) -> None:
    assert expose("new", "github", "p", "-s", "repos_list_branches").exit_code == 0
    file = workspace / "expose" / "github" / "p.yaml"
    before = file.read_bytes()

    refused = expose("new", "github", "p", "-s", "repos_get_branch")
    assert refused.exit_code != 0 and "--force" in refused.output
    assert file.read_bytes() == before

    assert expose("new", "github", "p", "-s", "repos_get_branch", "--force").exit_code == 0
    assert profile(workspace, "p")["actions"] == ["repos_get_branch"]


def test_author_finds_action_names_by_keyword(workspace: Path) -> None:
    result = expose("search", "github", "list branches", "-n", "5")

    assert result.exit_code == 0, result.output
    assert "repos_list_branches  GET /repos/{owner}/{repo}/branches" in result.output
    rows = [line for line in result.output.splitlines() if re.match(r"[a-z0-9_]+  [A-Z]+\b", line)]
    assert len(rows) == 5  # long paths wrap onto a second line, so count result rows


def test_a_stale_profile_fails_the_check_until_resolved(workspace: Path) -> None:
    assert expose("new", "github", "github-branches", *BRANCHES).exit_code == 0
    file = workspace / "expose" / "github" / "github-branches.yaml"
    doc = profile(workspace, "github-branches")
    doc["actions"].remove("git_get_ref")  # as if written before the connector had it
    file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    stale = file.read_bytes()

    check = expose("resolve", "--check")
    assert check.exit_code == 1
    assert "expose/github/github-branches.yaml" in check.output.replace("\\", "/")
    assert file.read_bytes() == stale  # --check changes nothing

    assert expose("resolve").exit_code == 0
    assert profile(workspace, "github-branches")["actions"] == BRANCH_ACTIONS
    resolved = file.read_bytes()
    again = expose("resolve")
    assert again.exit_code == 0 and "up to date" in again.output
    assert file.read_bytes() == resolved
    assert expose("resolve", "--check").exit_code == 0


def test_a_hand_written_profile_naming_a_missing_action_fails_the_check(workspace: Path) -> None:
    folder = workspace / "expose" / "github"
    folder.mkdir(parents=True)
    (folder / "hand.yaml").write_text(
        "connector: github\nactions:\n  - repos_get_branch\n  - repos_get_brnach\n",
        encoding="utf-8",
    )

    result = expose("resolve", "--check")

    assert result.exit_code != 0
    assert "repos_get_brnach" in result.output


# --------------------------------------------------------------------------- run time


class FakeGitHub:
    """Stands in for api.github.com at the HTTP client; records every request it gets."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"number": 1, "ref": "refs/heads/main"})

    @property
    def calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, **kwargs, transport=httpx.MockTransport(fake.handle))

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", client)
    # The SSRF guard resolves api.github.com in DNS; the fake needs no network.
    monkeypatch.setattr("node_wire_runtime.rest.assert_safe_destination", AsyncMock())
    return fake


def serve_with(monkeypatch: pytest.MonkeyPatch, name: str, *selectors: str) -> list[str]:
    """Author ``name`` with ``nw expose new`` and make it this process's profile."""
    result = expose("new", "github", name, *selectors)
    assert result.exit_code == 0, result.output
    monkeypatch.setenv("NW_EXPOSE_PROFILE", name)
    return [f"github_{a}" for a in profile(Path.cwd(), name)["actions"]]


def mcp_host(tool_mode: str = "list") -> Any:
    return mcp_server.McpServer(
        connector_ids=["github"], tool_mode=tool_mode
    )._setup_lowlevel_server()


async def list_tools(host: Any) -> set[str]:
    result = await host.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list")
    )
    return {t.name for t in result.root.tools if t.name.startswith("github_")}


async def call_tool(host: Any, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    result = await host.request_handlers[types.CallToolRequest](
        types.CallToolRequest(
            method="tools/call", params=types.CallToolRequestParams(name=name, arguments=arguments)
        )
    )
    return result.root


def text(result: types.CallToolResult) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


async def test_mcp_lists_searches_and_calls_only_the_profile(
    workspace: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    exposed = serve_with(monkeypatch, "github-prs", *PRS)
    host = mcp_host()

    assert await list_tools(host) == set(exposed)

    hidden = await call_tool(
        host, "github_pulls_merge", {"owner": "o", "repo": "r", "pull_number": 1}
    )
    assert hidden.isError and "UNKNOWN_TOOL" in text(hidden)
    assert github.calls == []  # never reached GitHub

    found = await call_tool(
        mcp_host("search"), "nw_search_tools", {"query": "merge a pull request"}
    )
    assert '"github_pulls_merge"' not in text(found)
    assert "github_pulls_" in text(found)  # the search works; it just cannot see hidden tools

    allowed = await call_tool(
        host, "github_pulls_get", {"owner": "o", "repo": "r", "pull_number": 1}
    )
    assert not allowed.isError, text(allowed)
    assert github.calls == [("GET", "/repos/o/r/pulls/1")]
    assert github.requests[0].headers["authorization"] == "Bearer test-token-not-real"


def rest_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A fresh REST app, built the way it is at startup."""
    monkeypatch.setattr(rest_app, "app", FastAPI())
    monkeypatch.setattr(rest_app, "_factory", None)
    monkeypatch.setattr(rest_app, "_exposure", None)
    rest_app._build_dynamic_routes()
    return TestClient(rest_app.app)


def test_rest_serves_and_documents_only_the_profile(
    workspace: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    exposed = {name.removeprefix("github_") for name in serve_with(monkeypatch, "github-prs", *PRS)}
    client = rest_client(monkeypatch)

    documented = {
        path.rsplit("/", 1)[1]
        for path in client.get("/openapi.json").json()["paths"]
        if path.startswith("/connectors/github/")
    }
    assert documented == exposed

    hidden = client.post(
        "/connectors/github/pulls_merge", json={"owner": "o", "repo": "r", "pull_number": 1}
    )
    assert hidden.status_code == 404
    assert github.calls == []

    allowed = client.post(
        "/connectors/github/pulls_get", json={"owner": "o", "repo": "r", "pull_number": 1}
    )
    assert allowed.status_code == 200, allowed.text
    assert github.calls == [("GET", "/repos/o/r/pulls/1")]


async def test_grpc_refuses_a_hidden_action_and_runs_an_exposed_one(
    workspace: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_with(monkeypatch, "github-branches", *BRANCHES)
    service = ConnectorServiceServicer()

    def request(action: str, payload: dict[str, Any]) -> Any:
        return connector_pb2.InvokeRequest(
            connector_id="github", action=action, payload_json=json.dumps(payload)
        )

    hidden = await service._invoke_async(
        request("pulls_merge", {"owner": "o", "repo": "r", "pull_number": 1})
    )
    assert (hidden.success, hidden.error_code, hidden.error_category) == (
        False,
        "ACTION_NOT_EXPOSED",
        "BUSINESS",
    )
    assert "pulls_merge" in hidden.message
    assert github.calls == []

    allowed = await service._invoke_async(
        request("git_get_ref", {"owner": "o", "repo": "r", "ref": "heads/main"})
    )
    assert allowed.success, allowed.message
    assert github.calls == [("GET", "/repos/o/r/git/ref/heads/main")]


def test_startup_log_shows_the_profile_and_its_counts_by_method(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    serve_with(monkeypatch, "github-branches", *BRANCHES)

    with caplog.at_level(logging.INFO, logger="bindings.invoke"):
        mcp_host()

    assert "exposure: profile github-branches" in caplog.text
    assert (
        f"exposure: github: 9 of {TOTAL} actions (DELETE 1, GET 4, PATCH 1, POST 2, PUT 1)"
        in caplog.text
    )


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("no-such-profile", "no loaded connector has"),
        ("../github-branches", "must be a profile name, not a path"),
        ("broken", "did you mean"),
    ],
    ids=["unknown-name", "path-like-name", "action-the-connector-lacks"],
)
def test_server_refuses_to_start_on_a_bad_profile(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, name: str, message: str
) -> None:
    serve_with(monkeypatch, "broken", "-s", "repos_get_branch")
    file = workspace / "expose" / "github" / "broken.yaml"
    file.write_text(
        file.read_text(encoding="utf-8").replace("- repos_get_branch", "- repos_get_brnach"),
        encoding="utf-8",
    )
    monkeypatch.setenv("NW_EXPOSE_PROFILE", name)

    with pytest.raises(ExposureConfigError, match=message):
        mcp_host()


async def test_without_a_profile_every_action_is_served(workspace: Path) -> None:
    assert len(await list_tools(mcp_host())) == TOTAL

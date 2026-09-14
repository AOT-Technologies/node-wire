#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Verify a running nw-mcp-builder MCP container advertises exactly the tools
its generated connector actually registers.

Used by the pet-store MCP-builder end-to-end job
(.github/workflows/mcp-builder-e2e.yml), after `nw gen-all` + `nw docker-build`
+ `docker run` have produced a live container: talks real MCP (streamable-http,
JSON-RPC) to it and diffs `tools/list` against `discover_actions()` on the
freshly generated `logic.py` — the same source of truth nw-mcp-builder itself
trusts, so this doesn't reimplement or duplicate the tool-naming convention.

Every existing nw-cli / nw-mcp-builder unit test mocks out codegen, wheel
builds, and docker build (`skip_build_wheels=True`, patched `subprocess.Popen`,
patched `run_docker_build`); none of them boot a real container. This script
is what would have caught the container crash-loop / silently-empty-tools/list
class of regression fixed 2026-09-11 — that bug passed the entire mocked
suite and only shows up against a real, running image.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent


def _add_import_paths() -> None:
    """Make `nw_mcp_builder.from_connector` and `bindings.mcp_server.server`
    importable regardless of whether the caller ran this via `uv run` from
    repo root (editable installs cover it) or some other cwd."""
    for rel in ("src", "nw-mcp-builder/src"):
        path = str(REPO_ROOT / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


def expected_tool_names(connector_id: str, logic_path: Path) -> set[str]:
    _add_import_paths()
    from bindings.mcp_server.server import mcp_advertised_tool_name
    from nw_mcp_builder.from_connector import discover_actions

    actions = discover_actions(logic_path)
    return {mcp_advertised_tool_name(connector_id, action) for action in actions}


def _headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _initialize_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-builder-e2e", "version": "1.0"},
        },
    }


def wait_for_ready(base_url: str, *, timeout: float, poll_interval: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.post(base_url, json=_initialize_request(), headers=_headers(), timeout=5.0)
            if resp.status_code == 200:
                return
            last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(poll_interval)
    raise SystemExit(f"MCP server at {base_url} never became ready: {last_error}")


def fetch_tools_list(base_url: str) -> list[dict]:
    init_resp = httpx.post(base_url, json=_initialize_request(), headers=_headers(), timeout=10.0)
    init_resp.raise_for_status()
    session_id = init_resp.headers.get("Mcp-Session-Id")

    list_resp = httpx.post(
        base_url,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=_headers(session_id),
        timeout=10.0,
    )
    list_resp.raise_for_status()
    data = list_resp.json()
    if "error" in data:
        raise SystemExit(f"tools/list returned an error: {data['error']}")
    return data["result"]["tools"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connector-id", required=True)
    parser.add_argument(
        "--logic-path",
        type=Path,
        help="Path to the generated connector's logic.py "
        "(default: src/node_wire_<connector-id>/logic.py)",
    )
    parser.add_argument("--base-url", default="http://localhost:8081/mcp")
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="Seconds to wait for container readiness"
    )
    args = parser.parse_args()

    logic_path = args.logic_path or (
        REPO_ROOT / "src" / f"node_wire_{args.connector_id}" / "logic.py"
    )
    if not logic_path.is_file():
        raise SystemExit(f"Generated connector logic.py not found: {logic_path}")

    expected = expected_tool_names(args.connector_id, logic_path)
    if not expected:
        raise SystemExit(
            f"discover_actions() found zero actions in {logic_path} — nothing to verify."
        )
    print(f"Expected {len(expected)} tool(s) from {logic_path}:")
    for name in sorted(expected):
        print(f"  - {name}")

    wait_for_ready(args.base_url, timeout=args.timeout)
    tools = fetch_tools_list(args.base_url)
    actual = {t["name"] for t in tools}

    print(f"\nContainer advertised {len(actual)} tool(s) via tools/list at {args.base_url}:")
    for name in sorted(actual):
        print(f"  - {name}")

    if not actual:
        raise SystemExit(
            "\ntools/list returned zero tools. This is the known "
            "NW_MCP_SCOPE_POLICY_DEFAULT=deny-without-map failure mode for images — "
            "check NW_MCP_AUTH_DISABLED / NW_MCP_SCOPE_POLICY_DEFAULT on the container."
        )

    missing = expected - actual  # generated action never made it into tools/list
    extra = actual - expected  # advertised tool with no matching source action

    if missing or extra:
        if missing:
            print(
                f"\nMISSING (ported action not exposed via MCP): {sorted(missing)}", file=sys.stderr
            )
        if extra:
            print(
                f"\nEXTRA (advertised tool with no matching source action): {sorted(extra)}",
                file=sys.stderr,
            )
        raise SystemExit(1)

    print(
        f"\nPASS: all {len(expected)} tool(s) round-tripped through "
        "nw gen-all -> nw docker-build -> docker run -> MCP tools/list."
    )


if __name__ == "__main__":
    main()

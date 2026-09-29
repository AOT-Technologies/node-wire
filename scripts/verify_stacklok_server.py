#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Smoke-test a running ``nw gen-stacklok`` server the way a ToolHive tenant proxy calls it.

Sends ``Authorization: Bearer`` + ``X-Tenant-ID`` like ToolHive, then checks:
  - tools/list is exactly the scope's tools plus nw_list_configs / nw_select_config;
  - one read-only tool call succeeds against the tenant's upstream API;
  - a call without X-Tenant-ID is a tool error (TENANT_REQUIRED);
  - a request without a bearer token is rejected with 401.

Usage:
  scripts/verify_stacklok_server.py --scope SCOPE.yaml --tool find_pets_by_status \\
      --arguments '{"status": "available"}' [--url http://127.0.0.1:8100/mcp]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict

import httpx
import yaml

_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_CONFIG_TOOLS = {"nw_list_configs", "nw_select_config"}


class _Session:
    def __init__(self, client: httpx.Client, url: str, headers: Dict[str, str]) -> None:
        self._client, self._url, self._headers = client, url, headers
        self._sid: str | None = None
        self._id = 0

    def _post(self, body: Dict[str, Any]) -> httpx.Response:
        headers = {**_ACCEPT, **self._headers}
        if self._sid:
            headers["mcp-session-id"] = self._sid
        return self._client.post(self._url, headers=headers, content=json.dumps(body))

    def rpc(self, method: str, params: Dict[str, Any]) -> Any:
        self._id += 1
        response = self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        )
        response.raise_for_status()
        self._sid = self._sid or response.headers.get("mcp-session-id")
        data = [line for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1][5:] if data else response.text)

    def open(self) -> "_Session":
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "verify", "version": "1"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self


def _wait(client: httpx.Client, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.post(url, headers=_ACCEPT, content="{}")
            return
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8100/mcp")
    parser.add_argument(
        "--scope", required=True, help="The mcp-scope.yaml the server was built from"
    )
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--token", default="verify-token")
    parser.add_argument("--tool", required=True, help="A read-only tool to call")
    parser.add_argument("--arguments", default="{}", help="JSON arguments for --tool")
    parser.add_argument("--wait", type=float, default=60.0, help="Seconds to wait for the server")
    args = parser.parse_args()

    scope = yaml.safe_load(open(args.scope, encoding="utf-8"))
    expected = {t["tool_name"] for g in scope["groups"] for t in g["tools"]} | _CONFIG_TOOLS
    failures: list[str] = []
    with httpx.Client(timeout=60) as client:
        _wait(client, args.url, args.wait)
        auth = {"Authorization": f"Bearer {args.token}"}
        session = _Session(client, args.url, {**auth, "X-Tenant-ID": args.tenant}).open()

        listed = {t["name"] for t in session.rpc("tools/list", {})["result"]["tools"]}
        if listed != expected:
            failures.append(
                f"tools/list mismatch: missing {sorted(expected - listed)}, extra {sorted(listed - expected)}"
            )

        result = session.rpc(
            "tools/call", {"name": args.tool, "arguments": json.loads(args.arguments)}
        )["result"]
        if result.get("isError"):
            failures.append(f"{args.tool} failed: {result['content'][0]['text'][:300]}")

        no_tenant = _Session(client, args.url, auth).open()
        result = no_tenant.rpc(
            "tools/call", {"name": args.tool, "arguments": json.loads(args.arguments)}
        )["result"]
        if not result.get("isError") or "TENANT_REQUIRED" not in result["content"][0]["text"]:
            failures.append("a call without X-Tenant-ID was not rejected with TENANT_REQUIRED")

        status = client.post(
            args.url, headers={**_ACCEPT, "X-Tenant-ID": args.tenant}, content="{}"
        ).status_code
        if status != 401:
            failures.append(f"a request without a bearer token returned {status}, expected 401")

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(f"PASS: {len(listed)} tools listed; {args.tool} ok; tenant and bearer enforced")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

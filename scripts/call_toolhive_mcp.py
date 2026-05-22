#!/usr/bin/env python3
"""
Call a ToolHive MCP HTTP endpoint with an authorization token.

Default: tools/list, then post ``hi`` to Slack channel ``bot-testing``.
Use --tool-name to invoke a different tool instead.

Examples:
  # List tools (uses TOOLHIVE_MCP_URL and TOOLHIVE_MCP_BEARER_TOKEN from env)
  python scripts/call_toolhive_mcp.py

  python scripts/call_toolhive_mcp.py \\
    --url http://localhost:8081/mcp \\
    --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

  python scripts/call_toolhive_mcp.py \\
    --tool-name slack.post_message \\
    --arguments-json '{"channel":"C123","message":"hello"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import Any

import httpx

# Hardcoded demo post (default run after tools/list)
DEMO_SLACK_TOOL = "slack.post_message"
DEMO_SLACK_CHANNEL = "bot-testing"
DEMO_SLACK_MESSAGE = "hi"


class McpCallError(Exception):
    """HTTP or JSON-RPC failure when talking to the MCP endpoint."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _id_matches_response(request_id: str | None, response_id: Any) -> bool:
    if request_id is None or response_id is None:
        return False
    if request_id == response_id:
        return True
    if isinstance(response_id, str):
        return response_id.startswith(request_id) or request_id.startswith(response_id)
    return False


def _parse_response_body(body_text: str, *, request_id: str | None = None) -> dict[str, Any]:
    """
    Parse MCP HTTP bodies as either plain JSON or SSE (``data: {...}``) frames.
    """
    text = body_text.strip()
    if not text:
        return {}

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict):
                return data
            raise McpCallError(f"Unexpected JSON root type: {type(data).__name__}")

    messages: list[dict[str, Any]] = []
    for line in body_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
            messages.append(parsed)

    if not messages:
        looks_sse = "data:" in body_text
        fmt = "SSE (text/event-stream)" if looks_sse else "unknown"
        preview = body_text.strip().replace("\n", "\\n")[:500]
        raise McpCallError(f"Could not parse MCP response ({fmt}): {preview}")

    if request_id:
        for msg in messages:
            if _id_matches_response(request_id, msg.get("id")):
                return msg

    for msg in reversed(messages):
        if "result" in msg or "error" in msg:
            return msg

    return messages[-1]


class ToolHiveMcpCaller:
    """Minimal streamable-HTTP MCP client with token auth (headers + _meta)."""

    def __init__(self, base_url: str, auth_token: str | None, *, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token.strip() if auth_token else None
        self._timeout = timeout
        self._session_id: str | None = None
        self._initialized = False

    def _build_request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
            headers["X-API-Key"] = self._auth_token
        return headers

    def _inject_auth_meta(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._auth_token:
            return dict(params)
        out = dict(params)
        meta = out.get("_meta")
        merged: dict[str, Any] = dict(meta) if isinstance(meta, dict) else {}
        merged.setdefault("authorization", f"Bearer {self._auth_token}")
        merged.setdefault("Authorization", f"Bearer {self._auth_token}")
        merged.setdefault("x-api-key", self._auth_token)
        merged.setdefault("X-API-Key", self._auth_token)
        merged.setdefault("token", self._auth_token)
        merged.setdefault("api_key", self._auth_token)
        merged.setdefault("apiKey", self._auth_token)
        out["_meta"] = merged
        return out

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_id = payload.get("id")
        request_id = str(raw_id) if raw_id is not None else None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    self._base_url,
                    json=payload,
                    headers=self._build_request_headers(),
                )
            except httpx.RequestError as exc:
                raise McpCallError(f"Request failed: {exc}") from exc

            body_text = resp.text
            if resp.status_code >= 400:
                detail = body_text.strip() or resp.reason_phrase
                raise McpCallError(
                    f"HTTP {resp.status_code}: {detail}",
                    status_code=resp.status_code,
                )

            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id

            return _parse_response_body(body_text, request_id=request_id)

    async def initialize(self) -> None:
        if self._initialized:
            return

        init_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "call_toolhive_mcp", "version": "1.0.0"},
            },
        }
        data = await self._post_json(init_payload)
        if "error" in data:
            raise McpCallError(f"MCP initialize error: {data['error']}")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            await self._post_json(notif)
        except McpCallError:
            pass

        self._initialized = True

    async def rpc(self, method: str, params: dict[str, Any]) -> Any:
        await self.initialize()

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params or self._auth_token:
            payload["params"] = self._inject_auth_meta(params)

        data = await self._post_json(payload)
        if "error" in data:
            raise McpCallError(f"MCP {method} error: {data['error']}")
        return data.get("result")

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.rpc("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )


def _default_url() -> str | None:
    return (os.environ.get("TOOLHIVE_MCP_URL") or "").strip() or None


def _default_token() -> str | None:
    return (
        (os.environ.get("TOOLHIVE_MCP_BEARER_TOKEN") or "").strip()
        or (os.environ.get("TOOLHIVE_MCP_API_KEY") or "").strip()
        or None
    )


def _format_tool_call_result(result: Any) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, indent=2, default=str)

    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
            else:
                parts.append(json.dumps(block, indent=2, default=str))
        if parts:
            return "\n".join(parts)

    return json.dumps(result, indent=2, default=str)


def _print_tools(tools: list[dict[str, Any]], *, limit: int) -> None:
    print(f"OK: {len(tools)} tool(s)")
    for tool in tools[:limit]:
        name = tool.get("name", "<unnamed>")
        desc = (tool.get("description") or "").strip().split("\n", 1)[0]
        if desc:
            print(f"  - {name}: {desc[:120]}")
        else:
            print(f"  - {name}")
    if len(tools) > limit:
        print(f"  ... and {len(tools) - limit} more")


async def _run(args: argparse.Namespace) -> int:
    url = (args.url or _default_url() or "").strip()
    if not url:
        print(
            "ERROR: MCP URL required. Set TOOLHIVE_MCP_URL or pass --url.",
            file=sys.stderr,
        )
        return 2

    token = (args.token or _default_token() or "").strip() or None
    caller = ToolHiveMcpCaller(url, token, timeout=args.timeout)

    try:
        if args.tool_name:
            raw_args = args.arguments_json or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                print(f"ERROR: --arguments-json is not valid JSON: {exc}", file=sys.stderr)
                return 2
            if not isinstance(arguments, dict):
                print("ERROR: --arguments-json must be a JSON object", file=sys.stderr)
                return 2

            print(f"Calling {args.tool_name} ...")
            result = await caller.call_tool(args.tool_name, arguments)
            print("OK: tool call completed")
            print(_format_tool_call_result(result))
            return 0

        tools = await caller.list_tools()
        _print_tools(tools, limit=args.show)

        if not args.list_tools:
            print(
                f"Calling {DEMO_SLACK_TOOL} "
                f"(channel={DEMO_SLACK_CHANNEL!r}, message={DEMO_SLACK_MESSAGE!r}) ..."
            )
            result = await caller.call_tool(
                DEMO_SLACK_TOOL,
                {"channel": DEMO_SLACK_CHANNEL, "message": DEMO_SLACK_MESSAGE},
            )
            print("OK: demo message call completed")
            print(_format_tool_call_result(result))
        return 0

    except McpCallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.status_code in (401, 403):
            print("Hint: check --token or TOOLHIVE_MCP_BEARER_TOKEN", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Call ToolHive MCP with bearer/API token auth.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="MCP endpoint URL (default: TOOLHIVE_MCP_URL)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer/API token (default: TOOLHIVE_MCP_BEARER_TOKEN or TOOLHIVE_MCP_API_KEY)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List tools only; skip the hardcoded demo slack.post_message",
    )
    parser.add_argument(
        "--tool-name",
        default=None,
        help="Invoke tools/call for this tool name",
    )
    parser.add_argument(
        "--arguments-json",
        default="{}",
        help='JSON object for tool arguments (default: "{}")',
    )
    parser.add_argument(
        "--show",
        type=int,
        default=20,
        help="Max tool names to print in list mode (default: 20)",
    )
    args = parser.parse_args(argv)

    if args.list_tools and args.tool_name:
        print("ERROR: use either --list-tools or --tool-name, not both", file=sys.stderr)
        return 2

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

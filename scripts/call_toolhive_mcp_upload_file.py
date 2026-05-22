#!/usr/bin/env python3
"""
Upload a hardcoded text file to Slack via ToolHive MCP (slack.upload_file).

Sends ``helloworld.txt`` (content: ``helloworld``) to channel ``C0B4U96926A``.

Examples:
  python scripts/call_toolhive_mcp_upload_file.py \\
    --url http://127.0.0.1:26608/mcp \\
    --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

  # Uses TOOLHIVE_MCP_URL and TOOLHIVE_MCP_BEARER_TOKEN from env
  python scripts/call_toolhive_mcp_upload_file.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from call_toolhive_mcp import (  # noqa: E402
    McpCallError,
    ToolHiveMcpCaller,
    _default_token,
    _default_url,
    _format_tool_call_result,
)

DEMO_UPLOAD_TOOL = "slack.upload_file"
DEMO_UPLOAD_CHANNEL = "C0B4U96926A"
DEMO_UPLOAD_FILENAME = "helloworld.txt"
DEMO_UPLOAD_CONTENT = b"helloworld"
DEMO_UPLOAD_COMMENT = "Uploaded by call_toolhive_mcp_upload_file.py"


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

    content_b64 = base64.b64encode(DEMO_UPLOAD_CONTENT).decode("ascii")
    print(
        f"Calling {DEMO_UPLOAD_TOOL} "
        f"(channel={DEMO_UPLOAD_CHANNEL!r}, filename={DEMO_UPLOAD_FILENAME!r}, "
        f"bytes={len(DEMO_UPLOAD_CONTENT)}) ..."
    )

    try:
        result = await caller.call_tool(
            DEMO_UPLOAD_TOOL,
            {
                "channel": DEMO_UPLOAD_CHANNEL,
                "filename": DEMO_UPLOAD_FILENAME,
                "content_base64": content_b64,
                "initial_comment": DEMO_UPLOAD_COMMENT,
            },
        )
        print("OK: file upload call completed")
        print(_format_tool_call_result(result))
        return 0
    except McpCallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.status_code in (401, 403):
            print("Hint: check --token or TOOLHIVE_MCP_BEARER_TOKEN", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload helloworld.txt to Slack via ToolHive MCP.",
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
        default=120.0,
        help="HTTP timeout in seconds (default: 120)",
    )
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Generate demo MCP JWTs for Slack authorization testing.

Produces:
  - admin JWT with ``scopes: ["*"]`` (full access)
  - restricted JWT that blocks ``slack.upload_file``

Uses HS256 with the demo secret ``node-wire-demo-authz-2026``. For local/dev only;
do not use this secret in production.

Usage:
  python scripts/generate_demo_authz_tokens.py
  python scripts/generate_demo_authz_tokens.py --secret node-wire-demo-authz-2026
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

DEFAULT_SECRET = "node-wire-demo-authz-2026"
DEFAULT_TENANT = "demo"
TOKEN_TTL_HOURS = 24 * 365  # 1 year for local demos

SLACK_SCOPE_POST = "mcp:slack.post_message"
SLACK_SCOPE_DM = "mcp:slack.send_direct_message"
SLACK_SCOPE_UPLOAD = "mcp:slack.upload_file"

SLACK_SCOPE_MAP: dict[str, str] = {
    "slack.post_message": SLACK_SCOPE_POST,
    "slack.send_direct_message": SLACK_SCOPE_DM,
    "slack.upload_file": SLACK_SCOPE_UPLOAD,
}


def _base_claims(*, sub: str, tenant_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "sub": sub,
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }


def _admin_claims(tenant_id: str) -> dict[str, Any]:
    claims = _base_claims(sub="toolhive-admin", tenant_id=tenant_id)
    claims["scopes"] = ["*"]
    claims["blocked_scopes"] = []
    return claims


def _restricted_claims(tenant_id: str) -> dict[str, Any]:
    claims = _base_claims(sub="toolhive-restricted", tenant_id=tenant_id)
    claims["scopes"] = [SLACK_SCOPE_POST, SLACK_SCOPE_DM]
    claims["blocked_scopes"] = [SLACK_SCOPE_UPLOAD]
    return claims


def _encode(claims: dict[str, Any], secret: str) -> str:
    return jwt.encode(claims, secret, algorithm="HS256")


def _decode_preview(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate demo MCP JWTs for Slack authz.")
    parser.add_argument(
        "--secret",
        default=DEFAULT_SECRET,
        help=f"HS256 signing secret (default: {DEFAULT_SECRET!r})",
    )
    parser.add_argument(
        "--tenant-id",
        default=DEFAULT_TENANT,
        help=f"tenant_id claim (default: {DEFAULT_TENANT!r})",
    )
    args = parser.parse_args(argv)

    secret = args.secret.strip()
    if not secret:
        print("ERROR: --secret must not be empty", file=sys.stderr)
        return 2

    admin_claims = _admin_claims(args.tenant_id)
    restricted_claims = _restricted_claims(args.tenant_id)
    admin_token = _encode(admin_claims, secret)
    restricted_token = _encode(restricted_claims, secret)

    scope_map_json = json.dumps(SLACK_SCOPE_MAP, separators=(",", ":"))

    _print_section("MCP server (.env)")
    print("# Required for JWT auth on tools/call")
    print("NW_MCP_AUTH_ENABLED=true")
    print(f"NW_MCP_JWT_SECRET={secret}")
    print("NW_MCP_SCOPE_POLICY_DEFAULT=deny")
    print(f"NW_MCP_ACTION_SCOPE_MAP_JSON={scope_map_json}")

    _print_section("Admin token (scopes: *)")
    print(admin_token)
    print()
    print("Claims:")
    print(json.dumps(_decode_preview(admin_token, secret), indent=2))
    print()
    print("# ToolHive / client")
    print(f"TOOLHIVE_MCP_BEARER_TOKEN={admin_token}")

    _print_section("Restricted token (upload blocked)")
    print(restricted_token)
    print()
    print("Claims:")
    print(json.dumps(_decode_preview(restricted_token, secret), indent=2))
    print()
    print("# ToolHive / client")
    print(f"TOOLHIVE_MCP_BEARER_TOKEN={restricted_token}")

    _print_section("Expected behavior")
    print("- admin (*): all Slack actions allowed")
    print("- restricted: post_message and send_direct_message allowed")
    print("- restricted: upload_file denied (blocked_scopes wins over scopes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

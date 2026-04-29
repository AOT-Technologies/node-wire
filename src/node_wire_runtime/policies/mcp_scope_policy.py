from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from node_wire_runtime.policy import PolicyContext, PolicyDenied, PolicyHook

logger = logging.getLogger("runtime.policy.scope")


class ScopePolicyHook(PolicyHook):
    def __init__(self, action_scope_map: Mapping[str, str]) -> None:
        self._map = dict(action_scope_map)

    def check(self, context: PolicyContext) -> None:
        action_key = f"{context.connector_id}.{context.action}"
        required = self._map.get(action_key)
        logger.info(
            "Scope policy evaluating action",
            extra={
                "action_key": action_key,
                "required_scope": required or "",
                "principal": context.principal or "",
                "tenant_id": context.tenant_id or "",
                "scopes": list(context.scopes or ()),
            },
        )
        if not required:
            return
        scopes = set(context.scopes or ())
        if required in scopes or "*" in scopes:
            return
        raise PolicyDenied(f"Missing required scope: {required}")


def load_scope_map_from_env() -> dict[str, str]:
    raw = os.environ.get("NW_MCP_ACTION_SCOPE_MAP_JSON")
    if not raw:
        # Mirror MCP auth bootstrap behavior: recover config from project .env
        # when launch paths inherit incomplete shell env.
        repo_root_env = Path(__file__).resolve().parents[3] / ".env"
        load_dotenv(override=True)
        load_dotenv(repo_root_env, override=True)
        raw = os.environ.get("NW_MCP_ACTION_SCOPE_MAP_JSON")
    if not raw:
        logger.info("Scope policy map not configured (env empty)")
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("NW_MCP_ACTION_SCOPE_MAP_JSON must be a JSON object.")
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                "NW_MCP_ACTION_SCOPE_MAP_JSON must map string action keys to string scopes."
            )
        out[key] = value
    logger.info(
        "Scope policy map loaded",
        extra={"entries": len(out), "action_keys": sorted(out.keys())},
    )
    return out

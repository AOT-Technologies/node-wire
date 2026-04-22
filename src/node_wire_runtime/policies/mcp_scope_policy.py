from __future__ import annotations

import json
import os
from typing import Mapping

from node_wire_runtime.policy import PolicyContext, PolicyDenied, PolicyHook


class ScopePolicyHook(PolicyHook):
    def __init__(self, action_scope_map: Mapping[str, str]) -> None:
        self._map = dict(action_scope_map)

    def check(self, context: PolicyContext) -> None:
        required = self._map.get(f"{context.connector_id}.{context.action}")
        if not required:
            return
        scopes = set(context.scopes or ())
        if required in scopes or "*" in scopes:
            return
        raise PolicyDenied(f"Missing required scope: {required}")


def load_scope_map_from_env() -> dict[str, str]:
    raw = os.environ.get("NW_MCP_ACTION_SCOPE_MAP_JSON")
    if not raw:
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
    return out

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

from node_wire_runtime.policy import PolicyContext, PolicyDenied, PolicyHook

logger = logging.getLogger("runtime.policy.scope")

# Public for tests and MCP tool listing (must match hook behavior).
DEFAULT_SCOPE_MODE_ALLOW = "allow"
DEFAULT_SCOPE_MODE_DENY = "deny"

_warned_implicit_scope_default = False


def _truthy_default_mode(val: str) -> str:
    v = val.strip().lower()
    if v in ("deny", "default-deny", "closed"):
        return DEFAULT_SCOPE_MODE_DENY
    return DEFAULT_SCOPE_MODE_ALLOW


def load_scope_policy_default_from_env() -> str:
    """Return ``allow`` or ``deny`` from ``NW_MCP_SCOPE_POLICY_DEFAULT`` (default: deny)."""
    global _warned_implicit_scope_default
    raw = os.environ.get("NW_MCP_SCOPE_POLICY_DEFAULT")
    if not raw or not str(raw).strip():
        if not _warned_implicit_scope_default:
            logger.warning(
                "NW_MCP_SCOPE_POLICY_DEFAULT is unset; using code default 'deny'. "
                "Set NW_MCP_SCOPE_POLICY_DEFAULT explicitly and configure "
                "NW_*_API_KEY_SCOPES (or JWT scopes) for each transport."
            )
            _warned_implicit_scope_default = True
        return DEFAULT_SCOPE_MODE_DENY
    return _truthy_default_mode(str(raw))


def resolve_required_scope_for_action(
    *,
    connector_id: str,
    action: str,
    action_scope_map: Mapping[str, str],
    default_mode: str,
) -> Optional[str]:
    """
    Determine the scope string required for this action.

    - **allow**: only enforce when ``NW_MCP_ACTION_SCOPE_MAP_JSON`` has
      an entry for ``connector_id.action``.
    - **deny**: require either that explicit map entry or the conventional
      fallback ``mcp:<connector_id>.<action>``.
    """
    action_key = f"{connector_id}.{action}"
    explicit = action_scope_map.get(action_key)
    if explicit:
        return explicit
    if default_mode == DEFAULT_SCOPE_MODE_DENY:
        return f"mcp:{connector_id}.{action}"
    return None


def action_allowed_for_identity_scopes(
    *,
    connector_id: str,
    action: str,
    principal: Optional[str],
    tenant_id: Optional[str],
    scopes: Optional[tuple[str, ...]],
    action_scope_map: Mapping[str, str],
    default_mode: str,
) -> bool:
    """
    Same authorization decision as :class:`ScopePolicyHook` / ``tools/list`` filtering.

    Returns True if the action should be visible or executable for this caller.
    """
    required = resolve_required_scope_for_action(
        connector_id=connector_id,
        action=action,
        action_scope_map=action_scope_map,
        default_mode=default_mode,
    )
    scope_tuple = tuple(scopes or ())
    if required and not principal and not scope_tuple:
        logger.info(
            "Scope policy denied due to missing caller identity",
            extra={
                "action_key": f"{connector_id}.{action}",
                "required_scope": required,
            },
        )
        return False
    if not required:
        return True
    scope_set = set(scope_tuple)
    return required in scope_set or "*" in scope_set


class ScopePolicyHook(PolicyHook):
    def __init__(
        self,
        action_scope_map: Mapping[str, str],
        *,
        default_mode: str = DEFAULT_SCOPE_MODE_DENY,
    ) -> None:
        self._map = dict(action_scope_map)
        self._default_mode = (
            default_mode
            if default_mode in (DEFAULT_SCOPE_MODE_ALLOW, DEFAULT_SCOPE_MODE_DENY)
            else DEFAULT_SCOPE_MODE_DENY
        )

    def check(self, context: PolicyContext) -> None:
        action_key = f"{context.connector_id}.{context.action}"
        required = resolve_required_scope_for_action(
            connector_id=context.connector_id,
            action=context.action,
            action_scope_map=self._map,
            default_mode=self._default_mode,
        )
        scopes = tuple(context.scopes or ())
        if required and not context.principal and not scopes:
            logger.info(
                "Scope policy denied due to missing caller identity",
                extra={
                    "action_key": action_key,
                    "required_scope": required,
                },
            )
            raise PolicyDenied(f"Missing required scope: {required}")
        logger.info(
            "Scope policy evaluating action",
            extra={
                "action_key": action_key,
                "required_scope": required or "",
                "principal": context.principal or "",
                "tenant_id": context.tenant_id or "",
                "scopes": list(scopes),
            },
        )
        if not required:
            return
        scope_set = set(scopes)
        if required in scope_set or "*" in scope_set:
            return
        raise PolicyDenied(f"Missing required scope: {required}")


def load_scope_map_from_env() -> dict[str, str]:
    raw = os.environ.get("NW_MCP_ACTION_SCOPE_MAP_JSON")
    if not raw:
        # Mirror MCP auth bootstrap behavior: recover config from project .env
        # when launch paths inherit incomplete shell env. Use override=False so
        # explicitly set variables (e.g. pytest conftest, production injection) are not
        # stomped by repo .env — same as playground/scenarios load_dotenv().
        if os.environ.get("NW_REST_LOAD_DOTENV", "true").lower() not in ("0", "false", "no"):
            repo_root_env = Path(__file__).resolve().parents[3] / ".env"
            load_dotenv(override=False)
            load_dotenv(repo_root_env, override=False)
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

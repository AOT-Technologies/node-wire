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


def _truthy_default_mode(val: str | None) -> str:
    if val is None:
        return DEFAULT_SCOPE_MODE_ALLOW
    v = val.strip().lower()
    if v in ("deny", "default-deny", "closed"):
        return DEFAULT_SCOPE_MODE_DENY
    return DEFAULT_SCOPE_MODE_ALLOW


def load_scope_policy_default_from_env() -> str:
    """Return ``allow`` or ``deny`` from ``NW_MCP_SCOPE_POLICY_DEFAULT``."""
    raw = os.environ.get("NW_MCP_SCOPE_POLICY_DEFAULT")
    if not raw or not str(raw).strip():
        return DEFAULT_SCOPE_MODE_ALLOW
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

    - **allow** (default): only enforce when ``NW_MCP_ACTION_SCOPE_MAP_JSON`` has
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


def _evaluate_action_scope_access(
    *,
    required: Optional[str],
    principal: Optional[str],
    scopes: tuple[str, ...],
    blocked_scopes: tuple[str, ...],
    action_key: str,
) -> None:
    """
    Raise :class:`PolicyDenied` when the action is not allowed for caller scopes.

    Blocked scopes take precedence: if ``required`` is in ``blocked_scopes``, deny
    even when ``required`` or ``*`` is also present in ``scopes``.
    """
    if required and not principal and not scopes and not blocked_scopes:
        logger.info(
            "Scope policy bypassed due to missing caller identity",
            extra={"action_key": action_key, "required_scope": required},
        )
        return
    if not required:
        return
    blocked_set = set(blocked_scopes)
    if required in blocked_set:
        raise PolicyDenied(f"Scope blocked for this action: {required}")
    scope_set = set(scopes)
    if required in scope_set or "*" in scope_set:
        return
    raise PolicyDenied(f"Missing required scope: {required}")


def action_allowed_for_identity_scopes(
    *,
    connector_id: str,
    action: str,
    principal: Optional[str],
    tenant_id: Optional[str],
    scopes: Optional[tuple[str, ...]],
    blocked_scopes: Optional[tuple[str, ...]] = None,
    action_scope_map: Mapping[str, str],
    default_mode: str,
) -> bool:
    """
    Same authorization decision as :class:`ScopePolicyHook`.

    Returns True if the action should be executable for this caller.
    """
    required = resolve_required_scope_for_action(
        connector_id=connector_id,
        action=action,
        action_scope_map=action_scope_map,
        default_mode=default_mode,
    )
    try:
        _evaluate_action_scope_access(
            required=required,
            principal=principal,
            scopes=tuple(scopes or ()),
            blocked_scopes=tuple(blocked_scopes or ()),
            action_key=f"{connector_id}.{action}",
        )
        return True
    except PolicyDenied:
        return False


class ScopePolicyHook(PolicyHook):
    def __init__(
        self,
        action_scope_map: Mapping[str, str],
        *,
        default_mode: str = DEFAULT_SCOPE_MODE_ALLOW,
    ) -> None:
        self._map = dict(action_scope_map)
        self._default_mode = (
            default_mode
            if default_mode in (DEFAULT_SCOPE_MODE_ALLOW, DEFAULT_SCOPE_MODE_DENY)
            else DEFAULT_SCOPE_MODE_ALLOW
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
        blocked_scopes = tuple(context.blocked_scopes or ())
        logger.info(
            "Scope policy evaluating action",
            extra={
                "action_key": action_key,
                "required_scope": required or "",
                "principal": context.principal or "",
                "tenant_id": context.tenant_id or "",
                "scopes": list(scopes),
                "blocked_scopes": list(blocked_scopes),
            },
        )
        _evaluate_action_scope_access(
            required=required,
            principal=context.principal,
            scopes=scopes,
            blocked_scopes=blocked_scopes,
            action_key=action_key,
        )


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

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.tenant_session
=================================

Owns "which tenant/config is selected" for one MCP server session — the
in-memory overlay layered on top of :mod:`node_wire_runtime.identity`'s
stateless header/JWT/env-pin resolution.

:mod:`node_wire_runtime.identity` answers "what tenant does this one request
say it's for"; this module answers "what tenant has this session already
picked" (via the ``nw_select_tenant`` / ``nw_select_config`` tools or the
stdio ``NW_TENANT_ID`` env pin), plus the pin-lock and allowlist guardrails
around switching it.

One :class:`TenantSessionOverlay` per MCP server process (stdio and HTTP
share the same instance). Split ToolHive images do not share it — run one
MCP server with all connectors if session selection needs to be shared.

Collaborators (store lookups, config coverage) are injected rather than
imported, so this module has no dependency on ``bindings.mcp_server`` or
``bindings.factory`` and can be unit tested against fakes.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

from node_wire_runtime.config_store import DEFAULT_TENANT
from node_wire_runtime.identity import resolve_config_name

MISSING_TENANT_SELECT_MESSAGE = (
    "No tenant is pinned. Call nw_list_tenants, then nw_select_tenant "
    "with a tenant_id from that list."
)
PIN_LOCKED_MESSAGE = (
    "Tenant switch is disabled (NW_MCP_TENANT_PIN_LOCKED). "
    "Use NW_TENANT_ID (stdio) or X-Tenant-ID (HTTP) to pin the tenant."
)

# Collaborator shapes injected into TenantSessionOverlay.
StoreHasTenant = Callable[[str], bool]
ConfigCoverage = Callable[[str, str], Tuple[List[str], List[str]]]


def pin_locked_from_env() -> bool:
    """True when NW_MCP_TENANT_PIN_LOCKED forbids nw_select_tenant switches."""
    return os.getenv("NW_MCP_TENANT_PIN_LOCKED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def allowed_tenants_from_env() -> Optional[frozenset[str]]:
    """Tenant allowlist from NW_MCP_ALLOWED_TENANTS, or None for unrestricted."""
    raw = os.getenv("NW_MCP_ALLOWED_TENANTS", "").strip()
    if not raw:
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class TenantSessionOverlay:
    """The stateful "which tenant/config is selected" overlay for one MCP
    server process.
    """

    def __init__(
        self,
        *,
        store_has_tenant: StoreHasTenant,
        config_coverage: ConfigCoverage,
        pin_locked: Callable[[], bool] = pin_locked_from_env,
        allowed_tenants: Callable[[], Optional[frozenset[str]]] = allowed_tenants_from_env,
    ) -> None:
        self._store_has_tenant = store_has_tenant
        self._config_coverage = config_coverage
        self._pin_locked = pin_locked
        self._allowed_tenants = allowed_tenants
        self._env_pin: Optional[str] = None
        self._selected_tenant_id: Optional[str] = None
        self._selected_config_name: Optional[str] = None

    # ---- stdio env pin ----------------------------------------------- #

    def set_env_pin(self, tenant_id: Optional[str]) -> None:
        """Set the process-wide stdio pin (from ``NW_TENANT_ID``).

        HTTP transport never uses this — its per-request pin lives in
        ``server.py``'s ``_session_tenant_ctx``, deliberately outside this
        module (see the map's Notes: different in kind, no leak problem).
        """
        self._env_pin = tenant_id

    @property
    def env_pin(self) -> Optional[str]:
        return self._env_pin

    # ---- current selection (read) ------------------------------------ #

    @property
    def selected_tenant_id(self) -> Optional[str]:
        return self._selected_tenant_id

    @property
    def selected_config_name(self) -> Optional[str]:
        return self._selected_config_name

    # ---- guardrails ---------------------------------------------------- #

    def assert_switch_allowed(self) -> None:
        """Raise ``ValueError`` if ``NW_MCP_TENANT_PIN_LOCKED`` forbids
        switching tenants for this session."""
        if self._pin_locked():
            raise ValueError(PIN_LOCKED_MESSAGE)

    def assert_tenant_allowed(self, tenant_id: str) -> None:
        """Raise ``ValueError`` if ``tenant_id`` is unknown to the store or
        excluded by ``NW_MCP_ALLOWED_TENANTS``."""
        if not self._store_has_tenant(tenant_id):
            raise ValueError(
                f"Unknown tenant {tenant_id!r}. Call nw_list_tenants, then nw_select_tenant."
            )
        allowed = self._allowed_tenants()
        if allowed is not None and tenant_id not in allowed:
            raise ValueError(f"Tenant {tenant_id!r} is not allowed on this MCP server.")

    def filter_allowed_tenants(self, tenant_ids: List[str]) -> List[str]:
        """Narrow a tenant-id list to ``NW_MCP_ALLOWED_TENANTS``, if configured."""
        allowed = self._allowed_tenants()
        if allowed is None:
            return tenant_ids
        return [tid for tid in tenant_ids if tid in allowed]

    # ---- selection ------------------------------------------------------ #

    def set_selected_tenant(self, tenant_id: Optional[str]) -> None:
        """Directly pin the session's tenant, bypassing the switch-lock and
        allowlist guardrails that :meth:`select_tenant` enforces.

        For trusted in-process embedders only (e.g.
        ``agents.toolhive.InProcessMcpClient``) — analogous to the stdio
        env-pin path (:meth:`set_env_pin`), which is also unguarded. Callers
        that go through the ``nw_select_tenant`` MCP tool must use
        :meth:`select_tenant` instead.
        """
        self._selected_tenant_id = tenant_id

    def set_selected_config(self, config_name: Optional[str]) -> None:
        """Directly pin the session's config name, bypassing the
        config-coverage validation :meth:`select_config` performs.

        Same trusted-caller carve-out as :meth:`set_selected_tenant`.
        """
        self._selected_config_name = config_name

    def select_tenant(self, tenant_id: str) -> None:
        """Pin the session to ``tenant_id`` (``nw_select_tenant``).

        Clears the selected config name if it doesn't exist for the new
        tenant. Raises ``ValueError`` if switching is pin-locked or
        ``tenant_id`` is unknown/disallowed.
        """
        self.assert_switch_allowed()
        self.assert_tenant_allowed(tenant_id)
        self._selected_tenant_id = tenant_id
        self.clear_config_if_missing(tenant_id)

    def select_config(self, tenant_id: str, config_name: str) -> Tuple[List[str], List[str]]:
        """Pin the session to ``config_name`` for the given, already-resolved
        ``tenant_id`` (``nw_select_config``).

        Returns ``(connectors_with_config, connectors_missing_config)``.
        Raises ``ValueError`` if no connector on ``tenant_id`` has this
        config name.
        """
        have, missing = self._config_coverage(tenant_id, config_name)
        if not have:
            raise ValueError(f"Unknown config {config_name!r} for tenant {tenant_id!r}.")
        self._selected_config_name = config_name
        return have, missing

    def clear_config_if_missing(self, tenant_id: str) -> None:
        """Clear the selected config name if ``tenant_id`` has no connector
        configured under it (called after a tenant switch)."""
        name = self._selected_config_name
        if not name:
            return
        have, _missing = self._config_coverage(tenant_id, name)
        if not have:
            self._selected_config_name = None

    # ---- resolution ------------------------------------------------------ #

    def effective_tenant_id(
        self,
        *,
        tenant_arg: Optional[str] = None,
        resolve_from_request: Callable[[], str],
    ) -> str:
        """Resolve the tenant id for a tool call, in priority order:
        explicit ``tenant_arg`` > session-selected tenant >
        ``resolve_from_request()`` (headers/JWT/env-pin, via
        :mod:`node_wire_runtime.identity`) > ``DEFAULT_TENANT`` (if it
        exists in the store and is allowed) > raise.

        ``resolve_from_request`` is injected so this module never touches
        transport-specific request state (HTTP headers/JWT/session context)
        directly — see ``server.py``'s ``_resolve_tool_tenant_id``.
        """
        if tenant_arg:
            self.assert_switch_allowed()
            self.assert_tenant_allowed(tenant_arg)
            return tenant_arg
        if self._selected_tenant_id:
            return self._selected_tenant_id
        try:
            return resolve_from_request()
        except ValueError:
            if self._store_has_tenant(DEFAULT_TENANT):
                try:
                    self.assert_tenant_allowed(DEFAULT_TENANT)
                except ValueError:
                    pass
                else:
                    return DEFAULT_TENANT
            raise ValueError(MISSING_TENANT_SELECT_MESSAGE)

    def pinned_tenant_id_or_none(
        self, *, resolve_from_request: Callable[[], str]
    ) -> Optional[str]:
        """Tolerant variant of :meth:`effective_tenant_id`: ``None`` instead
        of raising when no tenant can be resolved."""
        try:
            return self.effective_tenant_id(resolve_from_request=resolve_from_request)
        except ValueError:
            return None

    def effective_config_name(self) -> Optional[str]:
        """The session-selected config name, or ``None`` in single-tenant
        mode (delegates to :func:`node_wire_runtime.identity.resolve_config_name`)."""
        return resolve_config_name(self._selected_config_name)

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

One :class:`TenantSessionOverlay` per MCP server process. Where a selection
lives depends on the transport, through the injected ``session_key``:

* stdio (``session_key()`` returns ``None``): one process-wide
  :class:`Selection` — the process has exactly one client.
* HTTP (``session_key()`` returns the caller's session key): one
  :class:`Selection` per session in a bounded LRU map, so one caller's
  ``nw_select_config`` never changes another caller's calls.

Split ToolHive images do not share it — run one MCP server with all
connectors if session selection needs to be shared.

Collaborators (store lookups, config coverage) are injected rather than
imported, so this module has no dependency on ``bindings.mcp_server`` or
``bindings.factory`` and can be unit tested against fakes.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, Hashable, List, Optional, Tuple

from node_wire_runtime.config_store import DEFAULT_TENANT
from node_wire_runtime.errors import ErrorCode, NodeWireError
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
# Key of the caller's session, or None for the process-wide (stdio) selection.
SessionKey = Callable[[], Optional[Hashable]]

#: Upper bound on remembered HTTP sessions; the least recently used is dropped.
MAX_TRACKED_SESSIONS = 4096


@dataclass(frozen=True)
class Selection:
    """What one session has selected. Replaced whole, never mutated."""

    tenant_id: Optional[str] = None
    config_name: Optional[str] = None


NO_SELECTION = Selection()


def _process_scope() -> Optional[Hashable]:
    return None


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
    server process: one process-wide selection (stdio) or one per session
    (HTTP), chosen per call by ``session_key``.
    """

    def __init__(
        self,
        *,
        store_has_tenant: StoreHasTenant,
        config_coverage: ConfigCoverage,
        pin_locked: Callable[[], bool] = pin_locked_from_env,
        allowed_tenants: Callable[[], Optional[frozenset[str]]] = allowed_tenants_from_env,
        session_key: SessionKey = _process_scope,
        max_sessions: int = MAX_TRACKED_SESSIONS,
    ) -> None:
        self._store_has_tenant = store_has_tenant
        self._config_coverage = config_coverage
        self._pin_locked = pin_locked
        self._allowed_tenants = allowed_tenants
        self._session_key = session_key
        self._max_sessions = max_sessions
        self._env_pin: Optional[str] = None
        self._lock = threading.Lock()
        self._process_selection = NO_SELECTION
        self._sessions: OrderedDict[Hashable, Selection] = OrderedDict()

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
    def selection(self) -> Selection:
        """The calling session's selection (process-wide on stdio)."""
        key = self._session_key()
        if key is None:
            return self._process_selection
        with self._lock:
            found = self._sessions.get(key)
            if found is None:
                return NO_SELECTION
            self._sessions.move_to_end(key)
            return found

    @property
    def selected_tenant_id(self) -> Optional[str]:
        return self.selection.tenant_id

    @property
    def selected_config_name(self) -> Optional[str]:
        return self.selection.config_name

    def _store(self, selection: Selection) -> None:
        key = self._session_key()
        if key is None:
            self._process_selection = selection
            return
        with self._lock:
            self._sessions[key] = selection
            self._sessions.move_to_end(key)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def _with_tenant(self, selection: Selection, tenant_id: Optional[str]) -> Selection:
        """``selection`` moved to ``tenant_id``, dropping a config that tenant lacks."""
        config_name = selection.config_name
        if tenant_id and config_name:
            have, _missing = self._config_coverage(tenant_id, config_name)
            if not have:
                config_name = None
        return Selection(tenant_id=tenant_id, config_name=config_name)

    # ---- guardrails ---------------------------------------------------- #

    def assert_switch_allowed(self) -> None:
        """Raise ``NodeWireError`` (a ``ValueError``) if ``NW_MCP_TENANT_PIN_LOCKED`` forbids
        switching tenants for this session."""
        if self._pin_locked():
            raise NodeWireError(ErrorCode.TENANT_PIN_LOCKED, PIN_LOCKED_MESSAGE)

    def assert_tenant_allowed(self, tenant_id: str) -> None:
        """Raise ``NodeWireError`` (a ``ValueError``) if ``tenant_id`` is unknown to the store or
        excluded by ``NW_MCP_ALLOWED_TENANTS``."""
        if not self._store_has_tenant(tenant_id):
            raise NodeWireError(
                ErrorCode.TENANT_NOT_ALLOWED,
                f"Unknown tenant {tenant_id!r}. Call nw_list_tenants, then nw_select_tenant.",
            )
        allowed = self._allowed_tenants()
        if allowed is not None and tenant_id not in allowed:
            raise NodeWireError(
                ErrorCode.TENANT_NOT_ALLOWED,
                f"Tenant {tenant_id!r} is not allowed on this MCP server.",
            )

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
        :meth:`select_tenant` instead. Like every tenant change, a selected
        config the new tenant lacks is dropped.
        """
        self._store(self._with_tenant(self.selection, tenant_id))

    def set_selected_config(self, config_name: Optional[str]) -> None:
        """Directly pin the session's config name, bypassing the
        config-coverage validation :meth:`select_config` performs.

        Same trusted-caller carve-out as :meth:`set_selected_tenant`.
        """
        self._store(replace(self.selection, config_name=config_name))

    def select_tenant(self, tenant_id: str) -> None:
        """Pin the session to ``tenant_id`` (``nw_select_tenant``).

        Clears the selected config name if it doesn't exist for the new
        tenant. Raises ``ValueError`` if switching is pin-locked or
        ``tenant_id`` is unknown/disallowed.
        """
        self.assert_switch_allowed()
        self.assert_tenant_allowed(tenant_id)
        self._store(self._with_tenant(self.selection, tenant_id))

    def select_config(self, tenant_id: str, config_name: str) -> Tuple[List[str], List[str]]:
        """Pin the session to ``config_name`` for the given, already-resolved
        ``tenant_id`` (``nw_select_config``).

        Returns ``(connectors_with_config, connectors_missing_config)``.
        Raises ``ValueError`` if no connector on ``tenant_id`` has this
        config name.
        """
        have, missing = self._config_coverage(tenant_id, config_name)
        if not have:
            raise NodeWireError(
                ErrorCode.CONFIG_NOT_FOUND,
                f"Unknown config {config_name!r} for tenant {tenant_id!r}.",
            )
        self._store(replace(self.selection, config_name=config_name))
        return have, missing

    def clear_config_if_missing(self, tenant_id: str) -> None:
        """Clear the selected config name if ``tenant_id`` has no connector
        configured under it."""
        current = self.selection
        if current.config_name:
            revalidated = self._with_tenant(current, tenant_id)
            self._store(replace(current, config_name=revalidated.config_name))

    # ---- resolution ------------------------------------------------------ #

    def effective_tenant_id(
        self,
        *,
        tenant_arg: Optional[str] = None,
        request_tenant_id: Optional[str] = None,
        resolve_from_request: Callable[[], str],
    ) -> str:
        """Resolve the tenant id for a tool call, in priority order:
        explicit ``tenant_arg`` > ``request_tenant_id`` (an already-resolved
        per-request signal, e.g. HTTP ``X-Tenant-ID``/JWT via
        ``server.py``'s ``_session_tenant_ctx``) > session-selected tenant >
        ``resolve_from_request()`` (stdio env-pin fallback, via
        :mod:`node_wire_runtime.identity`) > ``DEFAULT_TENANT`` (if it
        exists in the store and is allowed) > raise.

        A live per-request signal outranks session-selection so one HTTP
        session's ``nw_select_tenant`` can't shadow another concurrent HTTP
        session's ``X-Tenant-ID`` on the same (process-wide) overlay — see
        the map at ``.scratch/mcp-tenant-config-per-request/``. stdio has no
        such signal (no per-request headers exist), so
        ``nw_select_tenant`` there still overrides the process env pin via
        ``resolve_from_request()``, unchanged.

        ``request_tenant_id``/``resolve_from_request`` are injected so this
        module never touches transport-specific request state (HTTP
        headers/JWT/session context) directly — see ``server.py``'s
        ``_resolve_tool_tenant_id``.
        """
        if tenant_arg:
            self.assert_switch_allowed()
            self.assert_tenant_allowed(tenant_arg)
            return tenant_arg
        if request_tenant_id:
            return request_tenant_id
        selected = self.selection.tenant_id
        if selected:
            # The tenant may have been deleted since it was selected.
            if not self._store_has_tenant(selected):
                raise NodeWireError(
                    ErrorCode.TENANT_NOT_ALLOWED,
                    f"Selected tenant {selected!r} no longer exists. "
                    "Call nw_list_tenants, then nw_select_tenant.",
                )
            return selected
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
            raise NodeWireError(ErrorCode.MISSING_TENANT, MISSING_TENANT_SELECT_MESSAGE)

    def pinned_tenant_id_or_none(
        self,
        *,
        request_tenant_id: Optional[str] = None,
        resolve_from_request: Callable[[], str],
    ) -> Optional[str]:
        """Tolerant variant of :meth:`effective_tenant_id`: ``None`` instead
        of raising when no tenant can be resolved."""
        try:
            return self.effective_tenant_id(
                request_tenant_id=request_tenant_id,
                resolve_from_request=resolve_from_request,
            )
        except ValueError:
            return None

    def effective_config_name(self, *, config_arg: Optional[str] = None) -> Optional[str]:
        """Resolve the config name for a tool call, in priority order:
        explicit ``config_arg`` (a per-request tool-call argument, mirroring
        REST's payload ``config_name`` field / gRPC's ``request.config_name``
        field) > session-selected config (``nw_select_config``) > ``None``
        (tenant default).

        Unlike :meth:`effective_tenant_id`, there's no header/env-pin
        equivalent for config — MCP tool-call arguments are the only
        per-request channel, so ``config_arg`` outranks the shared
        session-selection outright, with no separate "live request" tier to
        thread through. In single-tenant mode both are ignored (delegates to
        :func:`node_wire_runtime.identity.resolve_config_name`).
        """
        if config_arg:
            return resolve_config_name(config_arg)
        return resolve_config_name(self.selection.config_name)

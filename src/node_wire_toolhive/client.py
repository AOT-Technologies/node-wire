# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Run one node-wire connector's actions for a stacklok-built MCP server."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

from bindings.factory import ConnectorFactory
from bindings.invoke import invoke
from node_wire_runtime.auth.base import reset_upstream_bearer, set_upstream_bearer
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.identity import (
    is_multitenancy_enabled,
    resolve_config_name,
    resolve_tenant_id,
)
from node_wire_runtime.tenant_persistence import load_tenants
from node_wire_toolhive import request as _request
from node_wire_toolhive.relay import relay_auth_provider_hook

_PROTOCOL = "mcp"


class NodeWireToolError(ToolError):
    """A connector action failed; carries node-wire's error taxonomy for the MCP client."""

    def __init__(self, message: str, *, error_code: Optional[str] = None) -> None:
        self.error_code = error_code
        super().__init__(f"{error_code}: {message}" if error_code else message)


class NodeWireClient:
    """Executes actions of ``connector_id`` for the MCP request being handled.

    Per call it resolves the tenant from the request headers (ToolHive's tenant proxy sets
    ``X-Tenant-ID``), the session's selected config, and relays the request's upstream
    credential into the connector's auth placement. Built lazily on first use so importing the
    generated server never touches configuration.
    """

    def __init__(
        self,
        connector_id: str,
        *,
        config_path: str | Path | None = None,
        factory: ConnectorFactory | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._config_path = config_path
        self._factory = factory
        self._ready = factory is not None
        self._lock = threading.Lock()
        self._selected_config: Dict[str, str] = {}

    @property
    def factory(self) -> ConnectorFactory:
        if not self._ready:
            with self._lock:
                if not self._ready:
                    os.environ.setdefault("NW_ALLOWED_CONNECTORS", self._connector_id)
                    auto_register()
                    factory = ConnectorFactory(
                        self._config_path, auth_provider_hook=relay_auth_provider_hook()
                    )
                    factory.load()
                    load_tenants(factory.store)
                    self._factory = factory
                    self._ready = True
        assert self._factory is not None
        return self._factory

    # ---- tenant / config ---------------------------------------------- #

    def tenant_id(self) -> str:
        """Tenant for the current request (``__default__`` when multitenancy is off)."""
        if not _request.from_tenant_proxy():
            raise NodeWireToolError(
                "Request did not come through a tenant proxy", error_code="PROXY_AUTH_FAILED"
            )
        try:
            return resolve_tenant_id(headers=_request.request_headers())
        except ValueError as exc:
            raise NodeWireToolError(str(exc), error_code="TENANT_REQUIRED") from exc

    def config_names(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Named configs of this connector for ``tenant_id`` (name + default flag only)."""
        rows = []
        for doc in self.factory.store.list(tenant_id, self._connector_id):
            name = doc.get("name")
            if isinstance(name, str):
                rows.append({"name": name, "default": bool(doc.get("default"))})
        return rows

    def select_config(self, config_name: str) -> str:
        """Pin the current MCP session to ``config_name`` for this request's tenant."""
        if not is_multitenancy_enabled():
            raise NodeWireToolError("Named configs require NW_MULTITENANCY_ENABLED=true")
        tenant_id = self.tenant_id()
        if self.factory.store.get(tenant_id, self._connector_id, config_name) is None:
            raise NodeWireToolError(
                f"Unknown config {config_name!r} for tenant {tenant_id!r}",
                error_code="CONFIG_NOT_FOUND",
            )
        self._selected_config[_request.session_key()] = config_name
        return tenant_id

    def selected_config(self) -> Optional[str]:
        return resolve_config_name(self._selected_config.get(_request.session_key()))

    # ---- execution ------------------------------------------------------ #

    async def run(self, action: str, arguments: Dict[str, Any]) -> Any:
        """Run ``action`` with ``arguments`` (connector input field names); return its data."""
        tenant_id = self.tenant_id()
        payload = {k: v for k, v in arguments.items() if v is not None}
        token = set_upstream_bearer(_request.bearer_token())
        try:
            response = await invoke(
                self.factory,
                connector_id=self._connector_id,
                action=action,
                payload=payload,
                protocol=_PROTOCOL,
                tenant_id=tenant_id,
                config_name=self.selected_config(),
            )
        except NodeWireToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, never a crash
            raise NodeWireToolError(str(exc), error_code=type(exc).__name__) from exc
        finally:
            reset_upstream_bearer(token)
        if not response.success:
            raise NodeWireToolError(
                response.message or "Connector action failed", error_code=response.error_code
            )
        return response.data

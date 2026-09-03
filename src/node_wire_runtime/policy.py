#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:
    from node_wire_runtime.config_store import ConnectorConfigStore


@dataclass
class PolicyContext:
    connector_id: str
    action: str
    input_payload: Mapping[str, Any]
    principal: Optional[str] = None
    tenant_id: Optional[str] = None
    scopes: Optional[tuple[str, ...]] = None


class PolicyDenied(Exception):
    """Raised when a policy hook denies execution."""


class PolicyHook(ABC):
    """
    Pre-execution policy hook.

    Implementations can integrate RBAC, OPA, or PII/PHI checks. For the POC we
    will provide a simple allow-all implementation in Layer C and keep this
    contract stable.
    """

    @abstractmethod
    def check(self, context: PolicyContext) -> None:
        """
        Perform policy evaluation.
        Raise PolicyDenied with a human-readable message when execution is not allowed.
        """
        raise NotImplementedError


class TenantConfigHook(PolicyHook):
    """Defense in depth for ``configured = entitled``.

    The factory already fails closed when a scope has no config; this hook catches
    any code path that reached :meth:`BaseConnector.run` without factory resolution.
    """

    def __init__(self, store: "ConnectorConfigStore") -> None:
        self._store = store

    def check(self, context: PolicyContext) -> None:
        tenant_id = context.tenant_id or "__default__"
        if not self._store.has_config(tenant_id, context.connector_id):
            raise PolicyDenied(
                f"no config for tenant '{tenant_id}' / connector '{context.connector_id}'"
            )

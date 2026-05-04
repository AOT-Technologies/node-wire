#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Optional


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

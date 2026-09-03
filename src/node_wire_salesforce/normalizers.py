#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Salesforce-specific MCP argument normalizers.

Owned by this connector (see docs/adr/0002-connector-specific-logic-stays-in-the-connector.md).
Thin wrappers around this package's own coalesce_*_args helpers (schema.py) — previously
these lived in node_wire_runtime.mcp_normalizers and imported back into this connector,
a Layer A -> Layer B dependency running backwards.
"""

from __future__ import annotations

from typing import Any, Dict

from .schema import (
    coalesce_read_delete_args,
    coalesce_update_contact_args,
    coalesce_update_lead_args,
)


def normalize_salesforce_update_contact(args: Dict[str, Any]) -> None:
    """MCP ingress: coalesce contact update args to record_id + fields."""
    coalesced = coalesce_update_contact_args(dict(args))
    args.clear()
    args.update(coalesced)


def normalize_salesforce_update_lead(args: Dict[str, Any]) -> None:
    """MCP ingress: coalesce lead update args to record_id + fields."""
    coalesced = coalesce_update_lead_args(dict(args))
    args.clear()
    args.update(coalesced)


def normalize_salesforce_read_delete_contact(args: Dict[str, Any]) -> None:
    coalesced = coalesce_read_delete_args(dict(args), id_aliases=("contact_id", "id", "recordId"))
    args.clear()
    args.update(coalesced)


def normalize_salesforce_read_delete_lead(args: Dict[str, Any]) -> None:
    coalesced = coalesce_read_delete_args(dict(args), id_aliases=("lead_id", "id", "recordId"))
    args.clear()
    args.update(coalesced)

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
SMTP-specific MCP argument normalizers.

Owned by this connector (see docs/adr/0002-connector-specific-logic-stays-in-the-connector.md).
"""

from __future__ import annotations

from typing import Any, Dict


def _is_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def normalize_smtp_send_email(args: Dict[str, Any]) -> None:
    """Map common LLM aliases for smtp.send_email to SmtpSendInput fields."""
    if _is_missing_or_blank(args.get("from_email")):
        for alias in ("from", "sender", "from_addr"):
            if not _is_missing_or_blank(args.get(alias)):
                args["from_email"] = args[alias]
                break
    for alias in ("from", "sender", "from_addr"):
        args.pop(alias, None)

    for relay_key in ("host", "port", "use_tls"):
        args.pop(relay_key, None)

    if isinstance(args.get("to"), str):
        args["to"] = [args["to"]]

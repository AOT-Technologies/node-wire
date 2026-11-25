#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""SMTP-specific MCP argument normalizers. Owned by this connector."""

from __future__ import annotations

from typing import Any, Dict


def _is_missing_or_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


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

    for field in ("to", "cc", "bcc"):
        if isinstance(args.get(field), str):
            args[field] = [args[field]]

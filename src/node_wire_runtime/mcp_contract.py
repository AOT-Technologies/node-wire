#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
MCP contract flags: phased deprecation of legacy tool arguments.

Environment variables (enterprise rollout):

- ``NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD``: ``allow`` | ``warn`` | ``reject``
  - Legacy: ``action: "upload"`` in the tool payload for ``google_drive.files.upload``.
  - Default: ``warn`` (rewrite to canonical + log once per process is not required; use WARNING).
  - ``reject``: do not rewrite; authoritative tool name + ``enforce_authoritative_action`` fails.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger("runtime.mcp_contract")

ENV_LEGACY_GDRIVE_ACTION_UPLOAD = "NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD"


def legacy_gdrive_action_upload_mode() -> Literal["allow", "warn", "reject"]:
    raw = (os.environ.get(ENV_LEGACY_GDRIVE_ACTION_UPLOAD) or "warn").strip().lower()
    if raw in ("allow", "warn", "reject"):
        return raw  # type: ignore[return-value]
    logger.warning(
        "Invalid %s=%r; using 'warn'",
        ENV_LEGACY_GDRIVE_ACTION_UPLOAD,
        raw,
    )
    return "warn"


def log_legacy_gdrive_action_upload_usage() -> None:
    """Structured log line for metrics/aggregation (no PII)."""
    logger.info(
        "mcp.legacy.alias | alias=action_upload | tool=google_drive.files.upload",
        extra={
            "event": "mcp.legacy.alias",
            "alias": "action_upload",
            "tool": "google_drive.files.upload",
        },
    )

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Google Drive-specific MCP argument normalizers.

Owned by this connector (see docs/adr/0002-connector-specific-logic-stays-in-the-connector.md).
Includes the legacy ``action: "upload"`` alias deprecation flag (previously
node_wire_runtime.mcp_contract, which despite its generic-sounding name was
entirely Drive-specific — one env var for one legacy alias on one connector).

Environment variables (enterprise rollout):

- ``NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD``: ``allow`` | ``warn`` | ``reject``
  - Legacy: ``action: "upload"`` in the tool payload for ``google_drive.files.upload``.
  - Default: ``warn`` (rewrite to canonical + log once per process is not required; use WARNING).
  - ``reject``: do not rewrite; authoritative tool name + ``enforce_authoritative_action`` fails.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Literal

logger = logging.getLogger("connectors.google_drive")

ENV_LEGACY_GDRIVE_ACTION_UPLOAD = "NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD"


def _split_ids(value: Any) -> List[str]:
    """Turn comma-separated string or list into a list of non-empty IDs."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _is_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


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


def normalize_google_drive_files_upload(args: Dict[str, Any]) -> None:
    """
    Map common LLM mistakes for files.upload to FilesUploadOperation fields.
    Mutates args in place. Canonical keys already set on the root win over aliases/nesting.
    """
    media = args.get("media")
    if media is not None:
        if isinstance(media, dict):
            if _is_missing_or_blank(args.get("name")) and not _is_missing_or_blank(
                media.get("name")
            ):
                args["name"] = media.get("name")

            if _is_missing_or_blank(args.get("mime_type")):
                mt = media.get("mime_type") or media.get("mimeType")
                if not _is_missing_or_blank(mt):
                    args["mime_type"] = mt

            if _is_missing_or_blank(args.get("parents")):
                parents = media.get("parents")
                if isinstance(parents, list) and parents:
                    args["parents"] = parents
                elif isinstance(parents, str) and parents.strip():
                    args["parents"] = _split_ids(parents)

            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                b64 = media.get("content_base64") or media.get("base64") or media.get("data")
                if not _is_missing_or_blank(b64):
                    args["content_base64"] = b64
                else:
                    text = media.get("content") or media.get("text") or media.get("body")
                    if not _is_missing_or_blank(text):
                        args["content"] = text
        elif isinstance(media, str):
            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                if media.strip():
                    args["content"] = media

        args.pop("media", None)

    args.pop("media_body", None)

    nested = args.get("file")
    if isinstance(nested, dict):
        for key in ("name", "mime_type", "parents", "content", "content_base64"):
            if key in nested and _is_missing_or_blank(args.get(key)):
                args[key] = nested[key]
        if _is_missing_or_blank(args.get("mime_type")) and nested.get("mimeType"):
            args["mime_type"] = nested["mimeType"]
        args.pop("file", None)

    if not _is_missing_or_blank(args.get("mimeType")) and _is_missing_or_blank(
        args.get("mime_type")
    ):
        args["mime_type"] = args["mimeType"]
    args.pop("mimeType", None)

    if args.get("action") == "upload":
        mode = legacy_gdrive_action_upload_mode()
        if mode == "reject":
            logger.warning(
                "Rejected legacy action value 'upload' for google_drive.files.upload "
                "(set %s=allow or omit action; tool name is authoritative).",
                "NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD",
            )
        else:
            if mode == "warn":
                logger.warning(
                    "Deprecated: action 'upload' in google_drive.files.upload payload; "
                    "omit 'action' or use 'files.upload'. "
                    "Set NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD=reject to hard-fail."
                )
                log_legacy_gdrive_action_upload_usage()
            args["action"] = "files.upload"

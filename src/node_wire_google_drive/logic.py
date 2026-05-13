#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from node_wire_runtime import BaseConnector
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.sdk_action_spec import execute_spec_in_thread

from .action_spec import DEFAULT_LIST_FIELDS, GOOGLE_DRIVE_ACTION_SPECS
from .exceptions import (
    GoogleDriveAuthError,
    GoogleDriveBusinessError,
    GoogleDriveFatalError,
    GoogleDriveRateLimitError,
)
from .schema import GoogleDriveOperationOutput

logger = logging.getLogger("connectors.google_drive")

# Re-export for tests and callers that imported from logic.
__all__ = ["DEFAULT_LIST_FIELDS", "GoogleDriveConnector"]


class GoogleDriveConnector(BaseConnector):
    """
    Google Drive connector: Drive API v3 operations are driven by action specs
    (see action_spec.py) and thin @nw_action handlers for logging and dispatch.
    """

    connector_id = "google_drive"
    action = "execute"
    output_model = GoogleDriveOperationOutput
    action_specs = GOOGLE_DRIVE_ACTION_SPECS

    error_map = {
        GoogleDriveAuthError: (ErrorCategory.AUTH, "GDRIVE_AUTH"),
        GoogleDriveRateLimitError: (ErrorCategory.RETRYABLE, "GDRIVE_RATE_LIMIT"),
        GoogleDriveBusinessError: (ErrorCategory.BUSINESS, "GDRIVE_BUSINESS_RULE"),
        GoogleDriveFatalError: (ErrorCategory.FATAL, "GDRIVE_FATAL"),
    }

    def build_client(self) -> Any:
        import asyncio

        # get_client_credentials() is async; run it synchronously here since
        # build_client() is called from the synchronous get_client() accessor.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In an async context, we can't use run_until_complete.
                # Instead, fetch credentials synchronously via the underlying
                # ServiceAccountAuthProvider._build_credentials() pattern.
                # This code path is reached during connector initialisation
                # inside an async frame (e.g. in tests with pytest-asyncio).
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    creds = pool.submit(
                        lambda: asyncio.run(self._auth_provider.get_client_credentials())
                    ).result()
            else:
                creds = loop.run_until_complete(self._auth_provider.get_client_credentials())
        except RuntimeError:
            creds = asyncio.run(self._auth_provider.get_client_credentials())

        if creds is None:
            # Fallback for NoAuthProvider or unconfigured provider —
            # attempt direct secret resolution for backward compatibility.
            raw_sa = self.secret_provider.get_secret("GOOGLE_DRIVE_SA_JSON")
            try:
                from google.oauth2 import service_account  # type: ignore[import]
                import json as _json

                info = _json.loads(raw_sa)
                creds = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=["https://www.googleapis.com/auth/drive"],
                )
            except Exception:
                creds = service_account.Credentials.from_service_account_file(
                    raw_sa.strip(),
                    scopes=["https://www.googleapis.com/auth/drive"],
                )

        return build("drive", "v3", credentials=creds)

    def _translate_and_raise_http_error(self, exc: HttpError) -> None:
        status = exc.resp.status
        content_str = str(getattr(exc, "content", "") or "")

        if status in (401, 403):
            if "quotaExceeded" in content_str or "rateLimitExceeded" in content_str:
                raise GoogleDriveRateLimitError("Google Drive quota/rate limit exceeded") from exc
            raise GoogleDriveAuthError("Authentication or permissions failure") from exc

        if status == 429 or status >= 500:
            raise GoogleDriveRateLimitError("Upstream service unavailable or rate limited") from exc

        if status in (400, 404, 409):
            reason = getattr(exc, "reason", str(exc))
            raise GoogleDriveBusinessError(f"Business logic failure: {reason}") from exc

        raise GoogleDriveFatalError(f"Unhandled HttpError status {status}") from exc

    async def _execute_action_spec(
        self,
        action_name: str,
        params: Any,
        *,
        trace_id: str,
        log_extra: dict[str, Any] | None = None,
    ) -> GoogleDriveOperationOutput:
        spec = GOOGLE_DRIVE_ACTION_SPECS.get(action_name)
        if spec is None:
            raise ValueError(f"No action spec registered for {action_name!r}")
        drive = self.get_client()
        extra = {"trace_id": trace_id, **(log_extra or {})}
        logger.info("Google Drive %s", action_name, extra=extra)
        try:
            raw = await execute_spec_in_thread(drive, spec, params)
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=raw,
            description=f"Successfully executed {action_name}",
        )

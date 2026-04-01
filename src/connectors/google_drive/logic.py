from __future__ import annotations

import json
import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from runtime import SDKConnector
from runtime.models import ErrorCategory
from runtime.sdk_action_spec import execute_spec_in_thread

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


class GoogleDriveConnector(SDKConnector):
    """
    Google Drive connector: Drive API v3 operations are driven by action specs
    (see action_spec.py) and thin @sdk_action handlers for logging and dispatch.
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
        raw_sa = self.secret_provider.get_secret("GOOGLE_DRIVE_SA_JSON")
        try:
            info = json.loads(raw_sa)
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/drive"],
            )
        except json.JSONDecodeError:
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
                raise GoogleDriveRateLimitError(
                    "Google Drive quota/rate limit exceeded"
                ) from exc
            raise GoogleDriveAuthError("Authentication or permissions failure") from exc

        if status == 429 or status >= 500:
            raise GoogleDriveRateLimitError(
                "Upstream service unavailable or rate limited"
            ) from exc

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


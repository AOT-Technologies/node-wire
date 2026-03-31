from __future__ import annotations

import asyncio
import json
import base64
import logging
from typing import Any, Union

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from runtime import BaseConnector

from .exceptions import (
    GoogleDriveAuthError,
    GoogleDriveBusinessError,
    GoogleDriveFatalError,
    GoogleDriveRateLimitError,
)
from .schema import (
    FilesCreateOperation,
    FilesDeleteOperation,
    FilesGetOperation,
    FilesListOperation,
    FilesUpdateOperation,
    FilesUploadOperation,
    GoogleDriveOperationInput,
    GoogleDriveOperationOutput,
    PermissionsCreateOperation,
)

logger = logging.getLogger("connectors.google_drive")

# Performant default for files.list so the API returns only needed metadata.
DEFAULT_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, webViewLink)"

_OperationUnion = Union[
    FilesCreateOperation,
    FilesListOperation,
    PermissionsCreateOperation,
    FilesGetOperation,
    FilesUpdateOperation,
    FilesUploadOperation,
    FilesDeleteOperation,
]


class GoogleDriveConnector(
    BaseConnector[GoogleDriveOperationInput, GoogleDriveOperationOutput],
):
    """
    Google Drive connector for files and permissions operations.
    """

    connector_id = "google_drive"
    action = "execute"

    async def internal_execute(
        self, params: GoogleDriveOperationInput, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info(
            "Executing Google Drive operation",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": self.action,
                "action_type": params.root.action,
            },
        )

        drive = self._build_client()

        try:
            response = await asyncio.to_thread(
                self._dispatch_to_sdk, drive, params.root
            )
            return GoogleDriveOperationOutput(
                raw=response,
                description=f"Successfully executed {params.root.action}",
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected SDK failure",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": self.action,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise GoogleDriveFatalError(str(exc)) from exc

    def _dispatch_to_sdk(
        self, drive: Any, params: _OperationUnion
    ) -> dict[str, Any]:
        """Routes the strictly validated model to the correct SDK method."""
        if params.action == "files.create":
            body = {
                "name": params.name,
                "mimeType": params.mime_type,
                "parents": params.parents,
            }
            body = {k: v for k, v in body.items() if v is not None}
            return drive.files().create(body=body, fields='id, name, webViewLink',
                                supportsAllDrives=True,
                                ).execute()

        if params.action == "files.list":
            fields = params.fields or DEFAULT_LIST_FIELDS
            result = drive.files().list(
                pageSize=params.page_size,
                q=params.query,
                fields=fields,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            return result

        if params.action == "permissions.create":           
            body = {
                "role": params.role,
                "type": params.type,
                "emailAddress": params.email_address,
            }
            return drive.permissions().create(
                fileId=params.file_id,
                body=body,
                supportsAllDrives=True,
            ).execute()

        if params.action == "files.get":
            fields = params.fields or "id,name,mimeType,parents"
            return (
                drive.files()
                .get(
                    fileId=params.file_id,
                    fields=fields,
                    supportsAllDrives=True,
                )
                .execute()
            )

        if params.action == "files.update":
            body: dict[str, Any] = {}
            if params.name is not None:
                body["name"] = params.name
            if params.mime_type is not None:
                body["mimeType"] = params.mime_type

            kwargs: dict[str, Any] = {}
            if params.add_parents:
                kwargs["addParents"] = ",".join(params.add_parents)
            if params.remove_parents:
                kwargs["removeParents"] = ",".join(params.remove_parents)

            return (
                drive.files()
                .update(
                    fileId=params.file_id,
                    body=body,
                    **kwargs,
                    supportsAllDrives=True,
                )
                .execute()
            )

        if params.action == "files.upload":
            body = {
                "name": params.name,
                "mimeType": params.mime_type,
                "parents": params.parents,
            }
            body = {k: v for k, v in body.items() if v is not None}

            if params.content_base64 is not None:
                media_bytes = base64.b64decode(params.content_base64)
            elif params.content is not None:
                media_bytes = params.content.encode("utf-8")
            else:
                raise ValueError("Either content or content_base64 must be provided for files.upload")

            media = MediaInMemoryUpload(
                media_bytes,
                mimetype=params.mime_type,
                resumable=False,
            )

            return (
                drive.files()
                .create(
                    body=body,
                    media_body=media,
                    fields='id, name, webViewLink',                    
                    supportsAllDrives=True,
                )
                .execute()
            )

        if params.action == "files.delete":
            drive.files().update(fileId=params.file_id,
                                body={'trashed': True},
                                supportsAllDrives=True,
                                ).execute()
            return {"file_id": params.file_id, "status": "deleted"}

        raise ValueError(f"Unmapped action router: {params.action}")

    def _translate_and_raise_http_error(self, exc: HttpError) -> None:
        """Translates Google's dynamic HTTP errors into static taxonomy classes."""
        status = exc.resp.status
        content_str = str(getattr(exc, "content", "") or "")

        if status in (401, 403):
            if "quotaExceeded" in content_str or "rateLimitExceeded" in content_str:
                raise GoogleDriveRateLimitError(
                    "Google Drive quota/rate limit exceeded"
                ) from exc
            raise GoogleDriveAuthError(
                "Authentication or permissions failure"
            ) from exc

        if status == 429 or status >= 500:
            raise GoogleDriveRateLimitError(
                "Upstream service unavailable or rate limited"
            ) from exc

        if status in (400, 404, 409):
            reason = getattr(exc, "reason", str(exc))
            raise GoogleDriveBusinessError(
                f"Business logic failure: {reason}"
            ) from exc

        raise GoogleDriveFatalError(
            f"Unhandled HttpError status {status}"
        ) from exc

    def _build_client(self) -> Any:
        raw_sa = self.secret_provider.get_secret("GOOGLE_DRIVE_SA_JSON")
        try:
            info = json.loads(raw_sa)
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/drive"],
            )
        except json.JSONDecodeError:
            # Fallback: treat the secret as a file path
            creds = service_account.Credentials.from_service_account_file(
                raw_sa.strip(),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
        return build("drive", "v3", credentials=creds)

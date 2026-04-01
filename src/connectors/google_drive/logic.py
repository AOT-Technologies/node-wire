from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from runtime import SDKConnector, sdk_action
from runtime.models import ErrorCategory

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
    GoogleDriveOperationOutput,
    PermissionsCreateOperation,
)

logger = logging.getLogger("connectors.google_drive")

DEFAULT_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, webViewLink)"


class GoogleDriveConnector(SDKConnector):
    """
    Google Drive connector: each Drive operation is an @sdk_action method.
    """

    connector_id = "google_drive"
    action = "execute"
    output_model = GoogleDriveOperationOutput

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

    @sdk_action("files.create")
    async def files_create(
        self, params: FilesCreateOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info("Google Drive files.create", extra={"trace_id": trace_id})
        drive = self.get_client()
        body = {k: v for k, v in {
            "name": params.name,
            "mimeType": params.mime_type,
            "parents": params.parents,
        }.items() if v is not None}
        try:
            result = await asyncio.to_thread(
                lambda: drive.files().create(
                    body=body,
                    fields="id, name, webViewLink",
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed files.create"
        )

    @sdk_action("files.list")
    async def files_list(
        self, params: FilesListOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info("Google Drive files.list", extra={"trace_id": trace_id})
        drive = self.get_client()
        fields = params.fields or DEFAULT_LIST_FIELDS
        try:
            result = await asyncio.to_thread(
                lambda: drive.files().list(
                    pageSize=params.page_size,
                    q=params.query,
                    fields=fields,
                    pageToken=params.page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed files.list"
        )

    @sdk_action("files.get")
    async def files_get(
        self, params: FilesGetOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info(
            "Google Drive files.get",
            extra={"trace_id": trace_id, "file_id": params.file_id},
        )
        drive = self.get_client()
        fields = params.fields or "id,name,mimeType,parents"
        try:
            result = await asyncio.to_thread(
                lambda: drive.files().get(
                    fileId=params.file_id,
                    fields=fields,
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed files.get"
        )

    @sdk_action("files.update")
    async def files_update(
        self, params: FilesUpdateOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info(
            "Google Drive files.update",
            extra={"trace_id": trace_id, "file_id": params.file_id},
        )
        drive = self.get_client()
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
        try:
            result = await asyncio.to_thread(
                lambda: drive.files().update(
                    fileId=params.file_id,
                    body=body,
                    supportsAllDrives=True,
                    **kwargs,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed files.update"
        )

    @sdk_action("files.upload")
    async def files_upload(
        self, params: FilesUploadOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info("Google Drive files.upload", extra={"trace_id": trace_id})
        drive = self.get_client()
        body = {k: v for k, v in {
            "name": params.name,
            "mimeType": params.mime_type,
            "parents": params.parents,
        }.items() if v is not None}
        if params.content_base64 is not None:
            media_bytes = base64.b64decode(params.content_base64)
        elif params.content is not None:
            media_bytes = params.content.encode("utf-8")
        else:
            raise ValueError(
                "Either content or content_base64 must be provided for files.upload"
            )
        media = MediaInMemoryUpload(
            media_bytes,
            mimetype=params.mime_type,
            resumable=False,
        )
        try:
            result = await asyncio.to_thread(
                lambda: drive.files().create(
                    body=body,
                    media_body=media,
                    fields="id, name, webViewLink",
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed files.upload"
        )

    @sdk_action("files.delete")
    async def files_delete(
        self, params: FilesDeleteOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info(
            "Google Drive files.delete",
            extra={"trace_id": trace_id, "file_id": params.file_id},
        )
        drive = self.get_client()
        try:
            await asyncio.to_thread(
                lambda: drive.files().update(
                    fileId=params.file_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw={"file_id": params.file_id, "status": "deleted"},
            description="Successfully executed files.delete",
        )

    @sdk_action("permissions.create")
    async def permissions_create(
        self, params: PermissionsCreateOperation, *, trace_id: str
    ) -> GoogleDriveOperationOutput:
        logger.info(
            "Google Drive permissions.create",
            extra={"trace_id": trace_id, "file_id": params.file_id},
        )
        drive = self.get_client()
        body: dict[str, Any] = {
            "role": params.role,
            "type": params.type,
        }
        if params.email_address:
            body["emailAddress"] = params.email_address
        if params.domain:
            body["domain"] = params.domain
        try:
            result = await asyncio.to_thread(
                lambda: drive.permissions().create(
                    fileId=params.file_id,
                    body=body,
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=result, description="Successfully executed permissions.create"
        )

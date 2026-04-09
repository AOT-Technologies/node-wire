"""
Google Drive action specs: mapping from validated Pydantic inputs to Drive API v3 calls.

Used by GoogleDriveConnector to reduce per-action boilerplate while preserving
behavior (defaults, field masks, shared drives flags).
"""

from __future__ import annotations

import base64
from typing import Any, Dict

from googleapiclient.http import MediaInMemoryUpload
from pydantic import BaseModel

from node_wire_runtime.mcp_normalizers import normalize_google_drive_files_upload
from node_wire_runtime.sdk_action_spec import SdkActionSpec

from .schema import (
    FilesCreateOperation,
    FilesDeleteOperation,
    FilesGetOperation,
    FilesListOperation,
    FilesUpdateOperation,
    FilesUploadOperation,
    PermissionsCreateOperation,
)

DEFAULT_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, webViewLink)"

# Action name -> SdkActionSpec (matches @nw_action("...") strings)
GOOGLE_DRIVE_ACTION_SPECS: Dict[str, SdkActionSpec] = {}


def _register_files_create() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["files.create"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="create",
        body_from_model={
            "name": "name",
            "mime_type": "mimeType",
            "parents": "parents",
        },
        constant_kwargs={
            "fields": "id, name, webViewLink",
            "supportsAllDrives": True,
        },
        input_model=FilesCreateOperation,
    )


def _build_files_list_kwargs(_drive: Any, model: BaseModel) -> Dict[str, Any]:
    """Match legacy behavior: pass q/pageToken explicitly even when None."""
    p = model if isinstance(model, FilesListOperation) else FilesListOperation.model_validate(model)
    return {
        "pageSize": p.page_size,
        "q": p.query,
        "fields": p.fields or DEFAULT_LIST_FIELDS,
        "pageToken": p.page_token,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }


def _register_files_list() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["files.list"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="list",
        build_kwargs=_build_files_list_kwargs,
        input_model=FilesListOperation,
    )


def _register_files_get() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["files.get"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="get",
        kwargs_from_model={"file_id": "fileId"},
        computed_kwargs={
            "fields": lambda p: p.fields or "id,name,mimeType,parents",
        },
        constant_kwargs={"supportsAllDrives": True},
        input_model=FilesGetOperation,
    )


def _register_files_update() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["files.update"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="update",
        kwargs_from_model={"file_id": "fileId"},
        body_from_model={
            "name": "name",
            "mime_type": "mimeType",
        },
        computed_kwargs={
            "addParents": lambda p: ",".join(p.add_parents) if p.add_parents else None,
            "removeParents": lambda p: ",".join(p.remove_parents) if p.remove_parents else None,
        },
        constant_kwargs={"supportsAllDrives": True},
        include_empty_body=True,
        input_model=FilesUpdateOperation,
    )


def _build_upload_kwargs(drive: Any, model: BaseModel) -> Dict[str, Any]:
    params = (
        model
        if isinstance(model, FilesUploadOperation)
        else FilesUploadOperation.model_validate(model)
    )
    body = {
        k: v
        for k, v in {
            "name": params.name,
            "mimeType": params.mime_type,
            "parents": params.parents,
        }.items()
        if v is not None
    }
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
    return {
        "body": body,
        "media_body": media,
        "fields": "id, name, webViewLink",
        "supportsAllDrives": True,
    }


def _register_files_upload() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["files.upload"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="create",
        build_kwargs=_build_upload_kwargs,
        input_model=FilesUploadOperation,
        alias_tolerant=True,
        mcp_normalize=normalize_google_drive_files_upload,
    )


def _register_files_delete() -> None:
    def _post_delete(_result: Any, model: BaseModel) -> Dict[str, Any]:
        file_id = getattr(model, "file_id", None)
        return {"file_id": file_id, "status": "deleted"}

    GOOGLE_DRIVE_ACTION_SPECS["files.delete"] = SdkActionSpec(
        resource_segments=("files",),
        method_name="update",
        kwargs_from_model={"file_id": "fileId"},
        body_constant={"trashed": True},
        constant_kwargs={"supportsAllDrives": True},
        post_process=_post_delete,
        input_model=FilesDeleteOperation,
    )


def _register_permissions_create() -> None:
    GOOGLE_DRIVE_ACTION_SPECS["permissions.create"] = SdkActionSpec(
        resource_segments=("permissions",),
        method_name="create",
        kwargs_from_model={"file_id": "fileId"},
        body_from_model={
            "role": "role",
            "type": "type",
            "email_address": "emailAddress",
            "domain": "domain",
        },
        constant_kwargs={"supportsAllDrives": True},
        input_model=PermissionsCreateOperation,
    )


def _init_specs() -> None:
    _register_files_create()
    _register_files_list()
    _register_files_get()
    _register_files_update()
    _register_files_upload()
    _register_files_delete()
    _register_permissions_create()


_init_specs()

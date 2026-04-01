"""
FastMCP Server Entrypoint — Google Drive
========================================
Standalone MCP server exposing all Google Drive connector actions:

  • google_drive_files_create
  • google_drive_files_list
  • google_drive_permissions_create
  • google_drive_files_get
  • google_drive_files_update
  • google_drive_files_upload
  • google_drive_files_delete

Usage:
    python -m agents.google_drive_mcp
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.google_drive_mcp")


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("mcp SDK not installed. Run: pip install 'node-wire[agents]'") from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.google_drive.schema import GoogleDriveOperationInput

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-google-drive")

    def _get_connector():
        drive = factory._connectors.get("google_drive")
        if not drive:
            raise RuntimeError("google_drive connector not configured")
        return drive

    # ------------------------------------------------------------------
    # Tool: google_drive_files_upload
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_upload",
        description=(
            "Upload a new file with content to Google Drive. "
            "Returns the file ID and a shareable web view link."
        ),
    )
    async def google_drive_files_upload(
        name: str,
        mime_type: str = "text/plain",
        content: str = "",
        content_base64: str = "",
        parents: str = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        parents_list = [p.strip() for p in parents.split(",")] if parents else None

        payload: dict = {
            "action": "files.upload",
            "name": name,
            "mime_type": mime_type,
        }
        if parents_list:
            payload["parents"] = parents_list
        if content:
            payload["content"] = content
        if content_base64:
            payload["content_base64"] = content_base64

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)

        raw = result.raw
        return {
            "file_id": raw.get("id"),
            "file_name": raw.get("name"),
            "web_view_link": raw.get("webViewLink"),
            "description": result.description,
        }

    # ------------------------------------------------------------------
    # Tool: google_drive_files_list
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_list",
        description="List or search for files in Google Drive.",
    )
    async def google_drive_files_list(
        query: str = "",
        page_size: int = 10,
        fields: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        payload = {
            "action": "files.list",
            "page_size": page_size,
        }
        if query:
            payload["query"] = query
        if fields:
            payload["fields"] = fields

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    # ------------------------------------------------------------------
    # Tool: google_drive_files_create
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_create",
        description="Create an empty file or folder in Google Drive.",
    )
    async def google_drive_files_create(
        name: str,
        mime_type: str = "application/vnd.google-apps.folder",
        parents: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        parents_list = [p.strip() for p in parents.split(",")] if parents else None

        payload = {
            "action": "files.create",
            "name": name,
            "mime_type": mime_type,
        }
        if parents_list:
            payload["parents"] = parents_list

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    # ------------------------------------------------------------------
    # Tool: google_drive_files_get
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_get",
        description="Get a file's metadata by its ID in Google Drive.",
    )
    async def google_drive_files_get(
        file_id: str,
        fields: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        payload = {
            "action": "files.get",
            "file_id": file_id,
        }
        if fields:
            payload["fields"] = fields

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    # ------------------------------------------------------------------
    # Tool: google_drive_files_update
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_update",
        description="Update a file's metadata (e.g. rename or move folders) in Google Drive.",
    )
    async def google_drive_files_update(
        file_id: str,
        name: str = "",
        mime_type: str = "",
        add_parents: str = "",
        remove_parents: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        add_parents_list = [p.strip() for p in add_parents.split(",")] if add_parents else None
        remove_parents_list = [p.strip() for p in remove_parents.split(",")] if remove_parents else None

        payload = {
            "action": "files.update",
            "file_id": file_id,
        }
        if name:
            payload["name"] = name
        if mime_type:
            payload["mime_type"] = mime_type
        if add_parents_list:
            payload["add_parents"] = add_parents_list
        if remove_parents_list:
            payload["remove_parents"] = remove_parents_list

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    # ------------------------------------------------------------------
    # Tool: google_drive_files_delete
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_files_delete",
        description="Trash a file in Google Drive by its ID.",
    )
    async def google_drive_files_delete(
        file_id: str,
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        payload = {
            "action": "files.delete",
            "file_id": file_id,
        }

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    # ------------------------------------------------------------------
    # Tool: google_drive_permissions_create
    # ------------------------------------------------------------------
    @mcp.tool(
        name="google_drive_permissions_create",
        description="Create a permission for a file (share a file) in Google Drive.",
    )
    async def google_drive_permissions_create(
        file_id: str,
        role: str,
        type: str,
        email_address: str = "",
        domain: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = _get_connector()

        payload = {
            "action": "permissions.create",
            "file_id": file_id,
            "role": role,
            "type": type,
        }
        if email_address:
            payload["email_address"] = email_address
        if domain:
            payload["domain"] = domain

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)
        return result.raw

    logger.info(
        "Registered %d Google Drive MCP tools", 7
    )
    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-google-drive MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()

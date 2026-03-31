"""
FastMCP Server Entrypoint — Google Drive
=======================================
Standalone MCP server exposing only the Google Drive tool.

Usage:
    python -m agents.google_drive_mcp
"""
from __future__ import annotations

import logging
import os
import uuid

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

    @mcp.tool(
        name="google_drive_upload_file",
        description=(
            "Upload a text file to Google Drive. "
            "Returns the file ID and a shareable web view link."
        ),
    )
    async def google_drive_upload_file(
        file_name: str,
        content: str,
        folder_id: str = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
        mime_type: str = "text/plain",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        drive = factory._connectors.get("google_drive")
        if not drive:
            raise RuntimeError("google_drive connector not configured")

        payload: dict = {
            "action": "files.upload",
            "name": file_name,
            "mime_type": mime_type,
            "content": content,
        }
        if folder_id:
            payload["parents"] = [folder_id]

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)

        raw = result.raw
        return {
            "file_id": raw.get("id"),
            "file_name": raw.get("name"),
            "web_view_link": raw.get("webViewLink"),
            "description": result.description,
        }

    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-google-drive MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()


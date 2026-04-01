from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from bindings.factory import ConnectorFactory
from connectors import auto_register
from connectors.manifest import build_manifest
from runtime import SDKConnector

logger = logging.getLogger("bindings.mcp_server")


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


def _normalize_search_params_keys(sp: Dict[str, Any]) -> Dict[str, Any]:
    """Map legacy/LLM keys inside search_params to FHIR-friendly names."""
    if not sp:
        return {}
    out = dict(sp)
    # patientId is not a standard FHIR Patient search param; identifier is typical for MRN-style lookup
    if "patientId" in out and "identifier" not in out:
        out["identifier"] = out.pop("patientId")
    if "givenName" in out and "given" not in out:
        out["given"] = out.pop("givenName")
    if "familyName" in out and "family" not in out:
        out["family"] = out.pop("familyName")
    return out


def _is_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _normalize_google_drive_files_upload(args: Dict[str, Any]) -> None:
    """
    Map common LLM mistakes for files.upload to FilesUploadOperation fields.
    Mutates args in place. Canonical keys already set on the root win over aliases/nesting.
    """
    # Legacy alias: callers sometimes pass a `media` object/string (Google SDK-ish).
    # Our connector schema is strict (extra=forbid); normalize `media` into canonical
    # `content` (text) / `content_base64` (binary) + metadata, then drop it.
    media = args.get("media")
    if media is not None:
        # Metadata aliases under media
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

            # Content aliases under media (prefer binary if provided)
            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                b64 = (
                    media.get("content_base64")
                    or media.get("base64")
                    or media.get("data")
                )
                if not _is_missing_or_blank(b64):
                    args["content_base64"] = b64
                else:
                    text = media.get("content") or media.get("text") or media.get("body")
                    if not _is_missing_or_blank(text):
                        args["content"] = text
        elif isinstance(media, str):
            # Treat plain-string media as text content.
            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                if media.strip():
                    args["content"] = media

        args.pop("media", None)

    # Some clients also try `media_body` (googleapiclient kwarg). It is never part of
    # the MCP schema; drop it so canonical fields can validate.
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
        args["action"] = "files.upload"


def normalize_mcp_tool_arguments(
    connector_id: str, action: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Map legacy FastMCP / LLM aliases to canonical connector schema fields.

    Conservative: if canonical keys are already set, aliases are ignored.
    """
    args = dict(arguments)

    if connector_id in ("fhir_cerner", "fhir_epic") and action == "read_patient":
        if not (args.get("resource_id") or "").strip():
            pid = args.get("patient_id") or args.get("patientId")
            if pid is not None and str(pid).strip():
                args["resource_id"] = str(pid).strip()
        args.pop("patient_id", None)
        args.pop("patientId", None)
        if not args.get("family_name") and args.get("familyName"):
            args["family_name"] = args.pop("familyName")
        if not args.get("given_name") and args.get("givenName"):
            args["given_name"] = args.pop("givenName")
        if args.get("search_params") and isinstance(args["search_params"], dict):
            args["search_params"] = _normalize_search_params_keys(args["search_params"])

    elif connector_id in ("fhir_cerner", "fhir_epic") and action == "search_patients":
        if not args.get("resource_ids"):
            raw = args.get("patient_ids") or args.get("patientIds")
            ids = _split_ids(raw)
            if ids:
                args["resource_ids"] = ids
        args.pop("patient_ids", None)
        args.pop("patientIds", None)
        if not args.get("family_name") and args.get("familyName"):
            args["family_name"] = args.pop("familyName")
        if not args.get("given_name") and args.get("givenName"):
            args["given_name"] = args.pop("givenName")
        if args.get("search_params") and isinstance(args["search_params"], dict):
            args["search_params"] = _normalize_search_params_keys(args["search_params"])

    elif connector_id == "google_drive" and action == "files.upload":
        _normalize_google_drive_files_upload(args)

    return args


class McpServer:
    """
    Manifest-driven MCP server: tools come from connector metadata; execution
    dispatches through ConnectorFactory and connector.run().

    Use list_tools() / invoke_tool() for programmatic access, or run_stdio()
    for a full MCP stdio transport.
    """

    def __init__(
        self,
        *,
        server_name: str = "node-wire",
        connector_ids: Optional[List[str]] = None,
    ) -> None:
        self._server_name = server_name
        self._connector_ids: Optional[frozenset[str]] = (
            None if connector_ids is None else frozenset(connector_ids)
        )
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()

    def list_tools(self) -> List[Dict[str, Any]]:
        connectors = self._factory.list_for_protocol("mcp")
        manifest = build_manifest(connectors)
        tools: List[Dict[str, Any]] = []
        for entry in manifest:
            cid = entry["connector_id"]
            if self._connector_ids is not None and cid not in self._connector_ids:
                continue
            tools.append(
                {
                    "name": f"{cid}.{entry['action']}",
                    "description": f"{cid} {entry['action']} connector action",
                    "input_schema": entry["input_schema"],
                    "output_schema": entry["output_schema"],
                }
            )
        return tools

    async def invoke_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            connector_id, action = name.split(".", 1)
        except ValueError:
            raise ValueError("Tool name must be in the form '<connector>.<action>'")

        if self._connector_ids is not None and connector_id not in self._connector_ids:
            raise ValueError(
                f"Connector {connector_id!r} is not allowed on this MCP server."
            )

        connector = self._factory.get_for_protocol(connector_id, "mcp")
        if connector is None:
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")

        run_args = normalize_mcp_tool_arguments(connector_id, action, arguments)
        if isinstance(connector, SDKConnector):
            run_args.setdefault("action", action)

        response = await connector.run(run_args)
        return response.model_dump()

    async def _run_stdio_async(self) -> None:
        from mcp.server import NotificationOptions, Server as LowLevelServer
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool

        low = LowLevelServer(self._server_name)

        @low.list_tools()
        async def handle_list_tools() -> list[Tool]:
            out: list[Tool] = []
            for t in self.list_tools():
                kwargs: Dict[str, Any] = {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["input_schema"],
                }
                if t.get("output_schema") is not None:
                    kwargs["outputSchema"] = t["output_schema"]
                out.append(Tool(**kwargs))
            return out

        @low.call_tool()
        async def handle_call_tool(tool_name: str, arguments: dict) -> dict:
            return await self.invoke_tool(tool_name, arguments or {})

        async with stdio_server() as (read_stream, write_stream):
            await low.run(
                read_stream,
                write_stream,
                low.create_initialization_options(
                    notification_options=NotificationOptions()
                ),
            )

    def run_stdio(self) -> None:
        import anyio

        anyio.run(self._run_stdio_async)


if __name__ == "__main__":
    # Simple demo runner that prints tool list and exits.
    server = McpServer()
    print(json.dumps(server.list_tools(), indent=2))

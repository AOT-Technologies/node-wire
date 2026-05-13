"""
ToolHive Agent
==============
A ReAct-style AI agent that connects to an MCP server running in ToolHive,
discovers its tools, and orchestrates a healthcare workflow:

  1. Fetch patient details via fhir_cerner.read_patient / fhir_epic.read_patient (or search_* tools)
  2. Write a patient summary file via google_drive.files.upload
  3. Email the summary via smtp.send_email

The LLM backend is fully configurable via the LLM_PROVIDER env var.

Usage::

    python -m agents.toolhive \\
        --patient-id 12724066 \\
        --recipient-email user@example.com \\
        --drive-folder-id "1ABC..."   # optional

    # Swap to OpenAI:
    LLM_PROVIDER=openai python -m agents.toolhive --patient-id 12724066 ...

Environment variables:
    TOOLHIVE_MCP_URL : MCP proxy URL from ToolHive UI (e.g. http://localhost:PORT/mcp)
    TOOLHIVE_MCP_URLS: Comma-separated MCP proxy URLs (multi-server)
    TOOLHIVE_MCP_API_KEY: Optional inbound MCP auth key (sent as Bearer + X-API-Key)
    TOOLHIVE_MCP_BEARER_TOKEN: Optional inbound MCP bearer token (JWT/API key)
    TOOLHIVE_MAX_TOOL_FAILURES: Stop after this many failed invocations per tool name (default: 2)
    LLM_PROVIDER     : groq | openai | gemini | anthropic  (default: groq)
    GROQ_API_KEY     : (when using groq)
    OPENAI_API_KEY   : (when using openai)
    GEMINI_API_KEY   : (when using gemini)
    ANTHROPIC_API_KEY: (when using anthropic)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol
import re

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("agents.toolhive")


_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SMTP_EMAIL_FIELDS = {"from_email", "to", "cc", "bcc", "reply_to", "sender"}


def _redact_tool_args_for_log(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of *args* safe for logging.

    For SMTP tools only: replace any email address value with '[REDACTED]'
    so that recipient and sender identifiers are never written to logs.
    All other tool args pass through unchanged.
    """
    if not tool_name.startswith("smtp."):
        return args

    scrubbed: Dict[str, Any] = {}
    for key, value in args.items():
        if key in _SMTP_EMAIL_FIELDS:
            if isinstance(value, list):
                scrubbed[key] = ["[REDACTED]"] * len(value)
            elif isinstance(value, str) and _EMAIL_RE.search(value):
                scrubbed[key] = "[REDACTED]"
            else:
                scrubbed[key] = value
        else:
            scrubbed[key] = value
    return scrubbed


def truncate_tool_result_for_llm(text: str) -> str:
    """
    Cap tool output size sent to the LLM so providers with strict limits (e.g. Groq
    on-demand TPM) do not fail with 413 / oversized requests after large FHIR payloads.

    Full raw output remains in AgentStep.tool_result for logging; only the message
    passed back into the chat is truncated.

    Override with env TOOLHIVE_MAX_TOOL_RESULT_CHARS (default 12000). Use 0 to disable.
    """
    raw = (os.environ.get("TOOLHIVE_MAX_TOOL_RESULT_CHARS") or "12000").strip()
    try:
        max_chars = int(raw)
    except ValueError:
        max_chars = 12000
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return (
        text[:max_chars]
        + "\n\n[... truncated "
        + str(omitted)
        + " characters for LLM context limits; use visible fields for next steps.]"
    )


def resolve_max_tool_failures(override: Optional[int] = None) -> int:
    """
    Max failed tool invocations per tool name before aborting the agent run.
    ``override`` wins; otherwise ``TOOLHIVE_MAX_TOOL_FAILURES`` (default 2). Minimum 1.
    """
    if override is not None:
        return max(1, int(override))
    raw = (os.environ.get("TOOLHIVE_MAX_TOOL_FAILURES") or "2").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 2
    return max(1, n)


def _is_tool_failure(tool_result: str) -> bool:
    """True if MCP/connector reported a failed tool outcome (not empty success)."""
    if not tool_result or not tool_result.strip():
        return False
    t = tool_result.strip()
    if t.startswith("ERROR:"):
        return True
    low = t.lower()
    if "input validation error" in low:
        return True
    if "validation error" in low and "input" in low:
        return True
    if t.startswith("{"):
        try:
            data = json.loads(t)
            if isinstance(data, dict) and data.get("success") is False:
                return True
        except json.JSONDecodeError:
            pass
    return False


def _tool_failure_abort_message(tool_name: str, max_failures: int) -> str:
    return (
        f'The tool "{tool_name}" failed {max_failures} times in a row. '
        "Please check the parameters against the schema from tools/list, "
        "or tell me if I should use a different tool or approach."
    )


def _chunk_agent_text(text: str, chunk_size: int = 180) -> List[str]:
    """Split final assistant text into UI-friendly chunks for stream consumers."""
    if not text:
        return [""]
    chunks: List[str] = []
    current = ""
    for part in text.split(" "):
        candidate = f"{current} {part}".strip()
        if current and len(candidate) > chunk_size:
            chunks.append(current + " ")
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _stream_done_event(trace_id: str, *, success: bool) -> Dict[str, Any]:
    from node_wire_runtime.streaming import stream_completion_log
    stream_completion_log(trace_id, success, connector_id="agent", action="run_events")
    return {
        "type": "done",
        "trace_id": trace_id,
        "success": success,
        "message": f"Streaming completed. trace_id={trace_id}",
    }


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class AgentStep:
    step: int
    tool_called: Optional[str]
    tool_args: Dict[str, Any]
    tool_result: Optional[str]
    llm_thought: Optional[str]


@dataclass
class AgentRunResult:
    success: bool
    trace_id: str
    steps: List[AgentStep] = field(default_factory=list)
    final_answer: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Lightweight async MCP client (SSE / streamable-HTTP transport)
# ---------------------------------------------------------------------------


class McpClient(Protocol):
    async def list_tools(self) -> List[Dict[str, Any]]: ...

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str: ...


class ToolHiveMcpClient:
    """
    Minimal async MCP client that communicates with the ToolHive HTTP proxy.

    ToolHive wraps the stdio MCP server in an HTTP proxy. We send JSON-RPC
    requests to that proxy endpoint.  The client sends POST requests to
    ``{base_url}/messages`` and receives responses as Server-Sent Events or
    plain JSON.

    Newer ToolHive deployments use the MCP Streamable HTTP transport, which
    requires an ``initialize`` / ``notifications/initialized`` handshake before
    any other request.  The session ID returned in the ``Mcp-Session-Id``
    response header must be forwarded in all subsequent requests.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session_id: Optional[str] = None
        self._initialized: bool = False
        self._auth_token: Optional[str] = (
            os.environ.get("TOOLHIVE_MCP_BEARER_TOKEN")
            or os.environ.get("TOOLHIVE_MCP_API_KEY")
        )

    def _build_request_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        # For MCP auth-gated servers, send both forms for compatibility.
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
            headers["X-API-Key"] = self._auth_token
        return headers

    def _inject_auth_meta(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._auth_token:
            return dict(params)
        out = dict(params)
        meta = out.get("_meta")
        if isinstance(meta, dict):
            merged_meta = dict(meta)
        else:
            merged_meta = {}
        # Include common aliases to maximize compatibility with MCP servers.
        merged_meta.setdefault("authorization", f"Bearer {self._auth_token}")
        merged_meta.setdefault("Authorization", f"Bearer {self._auth_token}")
        merged_meta.setdefault("x-api-key", self._auth_token)
        merged_meta.setdefault("X-API-Key", self._auth_token)
        merged_meta.setdefault("token", self._auth_token)
        merged_meta.setdefault("api_key", self._auth_token)
        merged_meta.setdefault("apiKey", self._auth_token)
        out["_meta"] = merged_meta
        return out

    async def _initialize(self) -> None:
        """Send MCP initialize + initialized handshake; store session ID."""
        import httpx

        init_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "node-wire", "version": "1.0.0"},
            },
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._base_url,
                json=init_payload,
                headers=self._build_request_headers(),
            )
            resp.raise_for_status()
            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"MCP initialize error: {data['error']}")

            # Send the initialized notification (fire-and-forget; no id = notification)
            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            try:
                await client.post(self._base_url, json=notif, headers=self._build_request_headers())
            except Exception:
                pass  # Notifications have no response; ignore transport errors

        self._initialized = True

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        import httpx

        if not self._initialized:
            await self._initialize()

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        # Always include params when auth token is present so _meta is sent even
        # for methods like tools/list that otherwise pass {}.
        if params or self._auth_token:
            payload["params"] = self._inject_auth_meta(params)

        url = self._base_url
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=self._build_request_headers())
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result")

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        # MCP returns content as list of {type, text} blocks
        content = result.get("content", [])
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(parts)
        return str(content)


class MultiMcpClient:
    """
    Fan-out MCP client that merges tools from multiple MCP servers and routes
    tool calls to the correct upstream based on tool name.
    """

    def __init__(self, clients: List[McpClient]) -> None:
        if not clients:
            raise ValueError("MultiMcpClient requires at least one client")
        self._clients = clients
        self._tool_to_client_idx: Dict[str, int] = {}

    async def list_tools(self) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        tool_to_idx: Dict[str, int] = {}
        success_count = 0
        fail_count = 0

        for idx, c in enumerate(self._clients):
            try:
                tools = await c.list_tools()
                success_count += 1
            except Exception as exc:
                logger.warning("MultiMcpClient: client %d unreachable, skipping: %s", idx, exc)
                fail_count += 1
                continue
            for t in tools:
                name = t.get("name")
                if not name:
                    continue
                # First-writer wins on collisions (but collisions are unexpected).
                if name in tool_to_idx:
                    continue
                tool_to_idx[name] = idx
                merged.append(t)

        logger.info(
            "MultiMcpClient: %d/%d clients reachable, %d tools discovered",
            success_count,
            len(self._clients),
            len(merged),
        )
        self._tool_to_client_idx = tool_to_idx
        return merged

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        idx = self._tool_to_client_idx.get(name)
        if idx is None:
            # Fallback: probe sequentially (best-effort) so callers can call
            # without explicitly calling list_tools first.
            last_err: Optional[Exception] = None
            for c in self._clients:
                try:
                    return await c.call_tool(name, arguments)
                except Exception as exc:
                    last_err = exc
            raise RuntimeError(f"Tool not found on any MCP server: {name}") from last_err

        return await self._clients[idx].call_tool(name, arguments)


class StdioMcpClient:
    """
    MCP client that launches the server as a subprocess and talks via stdio.
    Useful for local manual testing without ToolHive.
    """

    def __init__(self, command: List[str]) -> None:
        self._command = command
        self._exit_stack = AsyncExitStack()
        self._session = None

    async def __aenter__(self) -> StdioMcpClient:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError("mcp SDK not installed.") from exc

        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=os.environ.copy(),
        )
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(params))
        self._read, self._write = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(self._read, self._write)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._exit_stack.aclose()

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self._session:
            raise RuntimeError("Client not initialised. Use 'async with'")
        resp = await self._session.list_tools()
        # Convert to simple tool list
        return [
            {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
            for t in resp.tools
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        if not self._session:
            raise RuntimeError("Client not initialised. Use 'async with'")
        resp = await self._session.call_tool(name, arguments)
        parts = [c.text for c in resp.content if hasattr(c, "text")]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# The Agent
# ---------------------------------------------------------------------------


class ToolHiveAgent:
    """
    ReAct-style agent that uses an LLM + MCP tools from ToolHive.

    The agent:
    1. Calls ``list_tools`` to build the tool manifest for the LLM.
    2. Enters a ReAct loop: send task + tools to LLM → if tool call →
       invoke tool → append result → repeat.
    3. Stops when the LLM returns a final answer (no tool calls) or
       ``max_steps`` is reached, or the same tool fails ``max_tool_failures`` times.
    """

    def __init__(
        self,
        mcp_client: McpClient,
        llm_provider: Any,  # BaseLLMProvider
        max_steps: int = 10,
        max_tool_failures: Optional[int] = None,
    ) -> None:
        self._mcp = mcp_client
        self._llm = llm_provider
        self._max_steps = max_steps
        self._max_tool_failures = resolve_max_tool_failures(max_tool_failures)
        self._system_prompt: str = (
            "You are a healthcare data assistant. You have access to tools for fetching "
            "patient data from Cerner FHIR and Epic FHIR, uploading files to Google Drive, and sending "
            "emails via SMTP.\n"
            "Tool names are `<connector_id>.<action>` (e.g. `fhir_cerner.read_patient`, "
            "`fhir_epic.read_patient`, `google_drive.files.upload`, `smtp.send_email`). "
            "Use exactly the names and JSON-schema arguments from tools/list.\n\n"
            "WORKFLOW (MUST EXECUTE SEQUENTIALLY, ONE STRICT STEP AT A TIME):\n"
            "When asked to 'Send patient summaries via email' or similar tasks, you MUST follow this exact flow in order. DO NOT parallelize these steps:\n"
            "  1. First turn: Obtain patient demographics from the EHR.\n"
            '     - If the user gave a Patient ID: call `fhir_cerner.read_patient` or `fhir_epic.read_patient` with JSON `{"resource_id": "<id>"}` (use Epic when the ID starts with \'e\'). Do NOT use search_patients for a known ID.\n'
            "     - If there is NO Patient ID but there IS a name: use name fields or `search_patients` per tools/list schema (e.g. `given_name`, `family_name`, `birthdate`, or valid `search_params`).\n"
            "     - Use `search_patients` only when you have no ID, or after `read_patient` failed and you need a fallback.\n"
            "     CRITICAL: If the user has NOT provided a patient ID or name in their message, you MUST ASK them for it. DO NOT call tools with a guessed or hallucinated ID like '12345'.\n"
            "  2. Second turn: Once you have the patient data from step 1, create a file on Google Drive containing the masked patient summary. Do NOT use placeholder content.\n"
            "     For `google_drive.files.upload`, pass a flat JSON object: `name`, `mime_type` (snake_case — not `mimeType`), `parents`, and `content` (or `content_base64`). "
            "If you include `action`, it must be exactly `files.upload`. Do not nest fields under a `file` object. Do NOT pass `media` / `media_body`.\n"
            "  3. Third turn: Once step 2 returns a shareable Drive URL (see `data.raw.webViewLink` from tool `google_drive.files.upload`), send an email with that exact link. Do NOT call the email tool until you have the link.\n"
            "     CRITICAL: You MUST ask the user for the recipient email address if they haven't provided it. DO NOT guess email addresses like 'recipient_email@example.com'.\n"
            "     CRITICAL: In the email body, you MUST insert the actual URL string returned from step 2 (e.g. 'https://drive.google.com/...'). Do NOT literally write the text '<web_view_link>'.\n\n"
            "DATA PRIVACY & MASKING — follow these strictly:\n"
            "- Before uploading ANY data to Google Drive or sending it via Email, you MUST apply masking to the ACTUAL patient data you retrieved from tools:\n"
            "  - Date of Birth (DOB): Replace the year with '****' (e.g., if DOB is 1985-12-31, write ****-12-31).\n"
            "  - Patient ID: Mask all but the first 3 digits (e.g., if ID is '8877665', write '887****').\n"
            "  - NEVER use the placeholder values ('1990-05-12', '12724066', or 'Name') in your reports - always use the real patient data masked accordingly.\n"
            "- EMAIL WORKFLOW: When sending patient details to an email recipient:\n"
            "  1. ALWAYS upload the masked patient summary to Google Drive first.\n"
            "  2. Use `data.raw.webViewLink` from the `google_drive.files.upload` tool result.\n"
            "  3. In the email body, provide that link instead of the actual data.\n"
            "  4. The email body should be professional: 'Patient data summary from the EHR is available at the following secure link: [Link]'\n\n"
            "PAGINATION HANDLING — IMPORTANT:\n"
            "- When tools return pagination metadata with 'next_page_token', you MUST call the same tool again with 'page_token' set to that value to get the next page.\n"
            "- Always check for pagination info in tool results and continue fetching pages until there's no 'next_page_token'.\n"
            "- For Google Drive tools: Use 'page_token' from previous result to get next page of files.\n"
            "- For FHIR search tools: Use pagination tokens to get complete result sets.\n\n"
            "GUARDRAILS:\n"
            "- NEVER hallucinate or make up patient details. DO NOT guess IDs like '12345'. If missing, ask the user.\n"
            "- NEVER use placeholders like 'to be updated later' or '<web_view_link>'.\n"
            "- If a tool requires data from a previous tool's output, you MUST WAIT for the previous tool to complete in a previous turn.\n"
            "- If the user provides a Patient ID, do NOT ask for their name or birthdate. The ID is perfectly sufficient.\n"
            "- Do not call the same tool twice unless the first call failed.\n"
            "- Before calling any tool, verify you have ALL required parameters.\n"
            "- If a tool call fails, explain the error clearly and ask the user how to proceed.\n"
            "- Always confirm what you've done after completing the requested actions.\n"
            "- Keep responses concise and professional.\n"
        )

    async def run(self, task: str) -> AgentRunResult:
        trace_id = str(uuid.uuid4())
        logger.info("Agent run started | trace_id=%s", trace_id)
        logger.info("Task: %s", task)

        # Import here to avoid circular dependency in tests
        from agents.llm_factory import LLMMessage

        result = AgentRunResult(success=False, trace_id=trace_id)

        # 1. Discover available tools
        try:
            tools = await self._mcp.list_tools()
            logger.info("Discovered %d MCP tools", len(tools))
        except Exception as exc:
            result.error = f"Failed to list MCP tools: {exc}"
            logger.error(result.error)
            return result

        # 2. Initialise conversation
        messages: List[LLMMessage] = [
            LLMMessage(
                role="system",
                content=self._system_prompt,
            ),
            LLMMessage(role="user", content=task),
        ]

        # 3. ReAct loop
        tool_failures: Dict[str, int] = {}
        abort_after_tool_failures = False

        for step_num in range(1, self._max_steps + 1):
            logger.info("Agent step %d / %d", step_num, self._max_steps)

            try:
                llm_resp = self._llm.chat_with_tools(messages, tools)
            except Exception as exc:
                result.error = f"LLM error at step {step_num}: {exc}"
                logger.error(result.error)
                return result

            # Track the assistant turn
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=llm_resp.content,
                    tool_calls=llm_resp.tool_calls,
                )
            )

            if not llm_resp.wants_tool_call:
                # LLM finished
                result.final_answer = llm_resp.content
                result.success = True
                logger.info("Agent finished after %d steps", step_num)
                break

            # Execute each tool call
            for tc in llm_resp.tool_calls:
                scrubbed_args = _redact_tool_args_for_log(tc.name, tc.arguments)
                logger.info("Calling tool: %s | args=%s", tc.name, scrubbed_args)
                agent_step = AgentStep(
                    step=step_num,
                    tool_called=tc.name,
                    tool_args=tc.arguments,
                    tool_result=None,
                    llm_thought=llm_resp.content,
                )

                try:
                    tool_result_str = await self._mcp.call_tool(tc.name, tc.arguments)
                    logger.info("Tool %s returned response of length: %d chars", tc.name, len(tool_result_str))
                    
                    
                    # --- AUTOMATIC PAGINATION TOKEN HANDLING ---
                    try:
                        result_data = json.loads(tool_result_str)
                        pagination_meta = result_data.get('data', {}).get('_server_pagination_metadata', {})
                        next_token = pagination_meta.get('next_page_token')
                        
                        if next_token:
                            print(f"\n=== PAGINATION TOKEN DETECTED ===", file=sys.stderr)
                            
                            # Add pagination info to tool result for LLM to see
                            pagination_info = (
                                f"\n\n[PAGINATION INFO]\n"
                                f"Items returned: {pagination_meta.get('items_returned')}\n"
                                f"Was truncated: {pagination_meta.get('was_truncated_by_server', False)}\n"
                                f"Next page token available: {next_token}\n"
                                f"To get next page, call the same tool with page_token='{next_token}'"
                            )
                            tool_result_str += pagination_info
                            print(f"=== ADDED PAGINATION INFO TO RESULT ===", file=sys.stderr)
                        else:
                            print(f"\n=== NO PAGINATION TOKEN FOUND ===", file=sys.stderr)
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"Error parsing pagination metadata: {e}", file=sys.stderr)
                    
                    print(f"=================================================\n", file=sys.stderr, flush=True)

                except Exception as exc:
                    tool_result_str = f"ERROR: {exc}"
                    logger.error("Tool %s failed: %s", tc.name, exc)

                agent_step.tool_result = tool_result_str
                result.steps.append(agent_step)

                llm_tool_content = truncate_tool_result_for_llm(tool_result_str)
                if len(llm_tool_content) < len(tool_result_str):
                    logger.info(
                        "Tool %s result truncated for LLM: %d -> %d chars",
                        tc.name,
                        len(tool_result_str),
                        len(llm_tool_content),
                    )

                messages.append(
                    LLMMessage(
                        role="tool",
                        content=llm_tool_content,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )

                if _is_tool_failure(tool_result_str):
                    tool_failures[tc.name] = tool_failures.get(tc.name, 0) + 1
                    if tool_failures[tc.name] >= self._max_tool_failures:
                        msg = _tool_failure_abort_message(tc.name, self._max_tool_failures)
                        result.error = msg
                        result.final_answer = msg
                        logger.warning("Stopping agent: %s", msg)
                        abort_after_tool_failures = True
                        break

            if abort_after_tool_failures:
                break
        else:
            # Hit max_steps without a final answer
            result.error = (
                f"Agent reached max_steps ({self._max_steps}) without completing the task."
            )
            logger.warning(result.error)

        from node_wire_runtime.streaming import stream_completion_log
        stream_completion_log(trace_id, result.success, connector_id="agent", action="run")
        return result

    async def run_events(self, task: str) -> AsyncIterator[Dict[str, Any]]:
        trace_id = str(uuid.uuid4())
        from node_wire_runtime.streaming import resolve_stream_buffer_ms, BufferedStreamIterator
        
        buffer_ms = resolve_stream_buffer_ms()
        iterator = self._run_events_inner(task, trace_id)
        
        if buffer_ms > 0:
            async for item in BufferedStreamIterator(iterator, buffer_ms, trace_id, connector_id="agent", action="run_events"):
                yield item
        else:
            async for item in iterator:
                yield item

    async def _run_events_inner(self, task: str, trace_id: str) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream agent progress events for web clients.

        Contract:
        - ``meta``: emitted once with ``trace_id``.
        - ``status``: informational progress text.
        - ``step``: emitted after each MCP tool call completes.
        - ``final_chunk``: chunks of the final assistant answer.
        - ``error``: recoverable terminal error text.
        - ``done``: always emitted at terminal completion; clients should stop
          loaders when this event arrives.
        Stream agent progress events as the ReAct loop runs.

        The LLM providers currently return complete assistant messages, so final
        answer chunks begin after the final LLM call completes. Tool-step events
        are emitted immediately after each MCP tool call completes.
        """
        logger.info("Streaming agent run started | trace_id=%s", trace_id)
        logger.info("Task: %s", task)

        from agents.llm_factory import LLMMessage

        yield {"type": "meta", "trace_id": trace_id}

        try:
            tools = await self._mcp.list_tools()
            logger.info("Discovered %d MCP tools", len(tools))
            yield {"type": "status", "message": f"Discovered {len(tools)} MCP tools"}
        except Exception as exc:
            error = f"Failed to list MCP tools: {exc}"
            logger.error(error)
            yield {"type": "error", "trace_id": trace_id, "message": error}
            yield _stream_done_event(trace_id, success=False)
            return

        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=self._system_prompt),
            LLMMessage(role="user", content=task),
        ]
        tool_failures: Dict[str, int] = {}

        for step_num in range(1, self._max_steps + 1):
            logger.info("Streaming agent step %d / %d", step_num, self._max_steps)
            yield {"type": "status", "message": f"Agent reasoning step {step_num}"}

            try:
                llm_resp = self._llm.chat_with_tools(messages, tools)
            except Exception as exc:
                error = f"LLM error at step {step_num}: {exc}"
                logger.error(error)
                yield {"type": "error", "trace_id": trace_id, "message": error}
                yield _stream_done_event(trace_id, success=False)
                return

            messages.append(LLMMessage(
                role="assistant",
                content=llm_resp.content,
                tool_calls=llm_resp.tool_calls,
            ))

            if not llm_resp.wants_tool_call:
                for chunk in _chunk_agent_text(llm_resp.content or ""):
                    yield {"type": "final_chunk", "content": chunk}
                yield _stream_done_event(trace_id, success=True)
                return

            abort_message: Optional[str] = None
            for tc in llm_resp.tool_calls:
                scrubbed_args = _redact_tool_args_for_log(tc.name, tc.arguments)
                logger.info("Calling tool: %s | args=%s", tc.name, scrubbed_args)

                try:
                    tool_result_str = await self._mcp.call_tool(tc.name, tc.arguments)
                    logger.info("Tool %s returned: %.200s", tc.name, tool_result_str)
                except Exception as exc:
                    tool_result_str = f"ERROR: {exc}"
                    logger.error("Tool %s failed: %s", tc.name, exc)

                yield {
                    "type": "step",
                    "step": step_num,
                    "tool": tc.name,
                    "args": tc.arguments,
                    "result": tool_result_str,
                }

                messages.append(LLMMessage(
                    role="tool",
                    content=truncate_tool_result_for_llm(tool_result_str),
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                if _is_tool_failure(tool_result_str):
                    tool_failures[tc.name] = tool_failures.get(tc.name, 0) + 1
                    if tool_failures[tc.name] >= self._max_tool_failures:
                        abort_message = _tool_failure_abort_message(tc.name, self._max_tool_failures)
                        logger.warning("Stopping streaming agent: %s", abort_message)
                        break

            if abort_message:
                for chunk in _chunk_agent_text(abort_message):
                    yield {"type": "final_chunk", "content": chunk}
                yield _stream_done_event(trace_id, success=False)
                return

        error = f"Agent reached max_steps ({self._max_steps}) without completing the task."
        logger.warning(error)
        for chunk in _chunk_agent_text(error):
            yield {"type": "final_chunk", "content": chunk}
        yield _stream_done_event(trace_id, success=False)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


async def _run_agent(args: argparse.Namespace) -> None:
    from agents.llm_factory import LLMProviderFactory

    llm_provider_name = os.environ.get("LLM_PROVIDER", "groq")
    logger.info("Creating LLM provider: %s", llm_provider_name)
    provider = LLMProviderFactory.create_from_env()

    if args.local:
        logger.info("Using local stdio transport (launching server as subprocess)")
        # Launch the mcp_entrypoint.py as a subprocess
        cmd = [sys.executable, "-m", "agents.mcp_entrypoint"]
        mcp_client_context = StdioMcpClient(cmd)
    else:
        urls = resolve_mcp_urls()
        if not urls:
            raise ValueError(
                "TOOLHIVE_MCP_URL (single) or TOOLHIVE_MCP_URLS (comma-separated) is not set. "
                "Find the proxy URL(s) in ToolHive UI → Installed → copy the endpoint(s), "
                "or use --local for testing without a proxy."
            )
        if len(urls) == 1:
            mcp_client_context = ToolHiveMcpClient(urls[0])
        else:
            mcp_client_context = MultiMcpClient([ToolHiveMcpClient(u) for u in urls])

    # Use the client (handle async context for stdio)
    if isinstance(mcp_client_context, StdioMcpClient):
        async with mcp_client_context as mcp_client:
            agent = ToolHiveAgent(
                mcp_client,
                provider,
                max_steps=args.max_steps,
                max_tool_failures=args.max_tool_failures,
            )
            await _execute_task(agent, args, llm_provider_name, "local-stdio")
    else:
        agent = ToolHiveAgent(
            mcp_client_context,
            provider,
            max_steps=args.max_steps,
            max_tool_failures=args.max_tool_failures,
        )
        await _execute_task(agent, args, llm_provider_name, ",".join(urls))


async def _execute_task(
    agent: ToolHiveAgent, args: argparse.Namespace, provider_name: str, mcp_info: str
) -> None:
    # Build the task prompt
    task_parts = [
        f"Patient ID: {args.patient_id}" if args.patient_id else "",
        f"Patient name — family: {args.patient_family}, given: {args.patient_given}"
        if args.patient_family
        else "",
        "Please:",
        "1. Fetch the patient's details from Cerner FHIR or Epic FHIR (if the ID starts with 'e').",
        f"2. Create a text file named 'patient_summary_{args.patient_id or args.patient_family}.txt' in Google Drive"
        + (f" in folder {args.drive_folder_id}" if args.drive_folder_id else "")
        + ".",
        f"3. Send an email to {args.recipient_email} with the subject "
        f"'Patient Summary' and the patient details in the body.",
        "After completing all steps, confirm what was done.",
    ]
    task = "\n".join(p for p in task_parts if p)

    print("\n" + "=" * 60)
    print("Node Wire ToolHive Agent")
    print(f"Provider : {provider_name}")
    print(f"MCP info : {mcp_info}")
    print("=" * 60)
    print(f"Task:\n{task}\n")

    run_result = await agent.run(task)

    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    if run_result.success:
        print(f"✅ Success  | trace_id={run_result.trace_id}")
        print(f"\nFinal Answer:\n{run_result.final_answer}")
    else:
        print(f"❌ Failed   | trace_id={run_result.trace_id}")
        print(f"Error: {run_result.error}")

    if run_result.steps:
        print(f"\nSteps executed ({len(run_result.steps)}):")
        for s in run_result.steps:
            status = "✓" if "ERROR" not in (s.tool_result or "") else "✗"
            print(f"  {status} Step {s.step}: {s.tool_called}")
            print(f"       args   : {json.dumps(s.tool_args, indent=None)[:120]}")
            print(f"       result : {(s.tool_result or '')[:120]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ToolHive AI Agent — Cerner/Epic FHIR → Google Drive → Email"
    )
    parser.add_argument("--patient-id", default="", help="Cerner or Epic FHIR Patient ID")
    parser.add_argument("--patient-family", default="", help="Patient family name (for search)")
    parser.add_argument("--patient-given", default="", help="Patient given name (for search)")
    parser.add_argument(
        "--recipient-email", required=True, help="Email address to send the summary to"
    )
    parser.add_argument(
        "--drive-folder-id",
        default=os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
        help="Google Drive folder ID (optional)",
    )
    parser.add_argument(
        "--max-steps", type=int, default=10, help="Maximum agent steps (default: 10)"
    )
    parser.add_argument(
        "--max-tool-failures",
        type=int,
        default=None,
        help="Stop after this many failed calls per tool name (default: env TOOLHIVE_MAX_TOOL_FAILURES or 2)",
    )
    parser.add_argument(
        "--local", action="store_true", help="Run against local server via stdio (no proxy)"
    )
    args = parser.parse_args()

    if not args.patient_id and not args.patient_family:
        parser.error("Provide either --patient-id or --patient-family")

    asyncio.run(_run_agent(args))


if __name__ == "__main__":
    main()


def resolve_mcp_urls() -> List[str]:
    """
    Resolve MCP proxy URL(s) from environment variables.

    - TOOLHIVE_MCP_URLS: comma-separated list (preferred for multi-server)
    - TOOLHIVE_MCP_URL: single URL (backward compatible)
    """
    raw = (os.environ.get("TOOLHIVE_MCP_URLS") or "").strip()
    if raw:
        return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    single = (os.environ.get("TOOLHIVE_MCP_URL") or "").strip()
    return [single.rstrip("/")] if single else []

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv
import os
import asyncio
from node_wire_runtime.base_connector import NestedConnectorActionError
from node_wire_runtime.caller_identity import CallerIdentity
from node_wire_runtime.errors import ErrorMapper
from node_wire_runtime.http_safety import SsrfBlockedError, assert_safe_destination
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.config_store import DEFAULT_TENANT, ConfigNotFoundError
from node_wire_runtime.identity import (
    MissingTenantError,
    TenantIdentityMismatchError,
    is_multitenancy_enabled,
    resolve_config_name,
    resolve_tenant_id,
)
from node_wire_fhir_epic.logic import FhirEpicConnector
from node_wire_fhir_epic.schema import (
    FhirDocumentReferenceSearchInput,
    FhirEncounterSearchInput,
    FhirPatientReadInput,
)
from node_wire_fhir_cerner.schema import (
    FhirCernerDocumentReferenceSearchInput,
    FhirCernerEncounterSearchInput,
    FhirCernerPatientReadInput,
)


from .ext_patient_viewer.schema import ExternalPatientViewerInput

if os.environ.get("NW_REST_LOAD_DOTENV", "true").lower() not in ("0", "false", "no"):
    load_dotenv(override=False)


ErrorMapper.register_global(ValidationError, ErrorCategory.BUSINESS, code="UNSUPPORTED_OPERATION")
ErrorMapper.register_global(ValueError, ErrorCategory.BUSINESS, code="VALIDATION_ERROR")


logger = logging.getLogger("playground.scenarios")
# Dedicated LLM traffic logger: which model is selected for each chat request.
llm_logger = logging.getLogger("agents.llm")

# Simplified: process-wide, not per-session — the playground is single-user local dev.
_last_llm_option: Optional[str] = None


router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class IncidentReportInput(BaseModel):
    title: str
    severity: str
    component: str
    description: str
    reported_by: str = "Demo User"


class ScenarioStep(BaseModel):
    name: str
    status: str  # "pending", "success", "error"
    details: Optional[str] = None
    display_name: Optional[str] = None  # For "Plain English" UI labels
    data: Optional[Any] = None


class ScenarioResponse(BaseModel):
    success: bool
    steps: List[ScenarioStep]
    final_resource_id: Optional[str] = None
    human_summary: Optional[str] = None  # Business-value summary
    error_message: Optional[str] = None
    trace_id: str


def _safe_error_return(
    e: Exception, steps: List[ScenarioStep], trace_id: str, step_msg: str
) -> ScenarioResponse:
    from node_wire_runtime.errors import ErrorMapper
    from node_wire_runtime.models import ErrorCategory

    log = logging.getLogger("playground.scenarios")

    # Scenario steps span multiple connectors, so there's no single connector_id to
    # scope this to — only the runtime-wide mappings registered above apply here.
    if isinstance(e, NestedConnectorActionError):
        # run() already mapped the failure; surface its code, category and message.
        failed = e.response
        category = failed.error_category.value if failed.error_category else None
        steps[-1].status = "error"
        steps[-1].details = f"[{category}] {failed.message}"
        steps[-1].data = {
            "error_code": failed.error_code,
            "error_category": category,
            "raw": {"error": failed.message},
        }
        log.warning(f"{step_msg}: {failed.error_code}")
        return ScenarioResponse(
            success=False, steps=steps, trace_id=trace_id, error_message=step_msg
        )

    mapped_err = ErrorMapper.resolve(e, connector_id="playground")
    safe_msg = (
        str(e)
        if mapped_err.category != ErrorCategory.FATAL
        else "An internal system error occurred."
    )

    if hasattr(e, "errors") and callable(getattr(e, "errors", None)):
        try:
            safe_msg = e.errors()[0].get("msg", "Schema validation failed")
        except Exception as detail_exc:
            log.debug("Could not extract validation error detail: %s", detail_exc)

    steps[-1].status = "error"
    steps[-1].details = f"[{mapped_err.category.value}] {safe_msg}"

    # Provide structured error data
    steps[-1].data = {
        "error_code": mapped_err.code,
        "error_category": mapped_err.category.value,
        "raw": {"error": safe_msg},
    }

    if mapped_err.category == ErrorCategory.BUSINESS:
        log.warning(f"{step_msg}: {safe_msg}")
    else:
        log.error(f"{step_msg}: {e}", exc_info=True)

    return ScenarioResponse(success=False, steps=steps, trace_id=trace_id, error_message=step_msg)


# The caller of the current scenario request, set by resolve_connector so run_action can pass
# it to the runtime (policy scopes, audit principal).
_scenario_caller: ContextVar[Optional[CallerIdentity]] = ContextVar(
    "_scenario_caller", default=None
)


async def run_action(connector: Any, input_data: Any) -> Any:
    """Run one connector action through ``run()`` and return its typed output.

    Goes through input validation, the policy hook, the circuit breaker and audit logging, like
    a ``/connectors`` call. Simplified: no retry loop of its own. ``run()`` already retries errors
    the runtime classes as transient (with backoff and the breaker); a blanket retry on top would
    also re-run permanent failures (bad config, auth, validation) and stack with that one.
    Failures raise ``PolicyDenied`` / ``NestedConnectorActionError``.
    """
    root = getattr(input_data, "root", input_data)
    who = _scenario_caller.get()
    return await connector.call_action(
        root.action,
        root.model_dump(mode="json"),
        principal=who.principal if who else None,
        scopes=who.scopes if who else None,
    )


# Share the REST binding factory so playground config CRUD (/v1/...) and scenarios
# see the same in-memory ConnectorConfigStore.
_playground_factory: Optional[Any] = None


def get_playground_factory() -> Any:
    """Reuse the REST API factory (same process, same config store)."""
    global _playground_factory
    if _playground_factory is None:
        from bindings.rest_api.app import get_factory

        _playground_factory = get_factory()
    return _playground_factory


async def resolve_connector(
    request: Request,
    connector_id: str,
    *,
    action: Optional[str] = None,
    config_name: Optional[str] = None,
) -> Any:
    """Resolve a tenant-scoped connector instance for the playground.

    Tenant comes from ``X-Tenant-ID`` (then JWT). When multitenancy is enabled,
    a tenant id is required. ``config_name`` may be supplied as a query param.
    """
    from bindings.rest_api.auth import get_rest_caller_identity

    factory = get_playground_factory()
    _scenario_caller.set(get_rest_caller_identity(request))
    if not factory.is_exposed(connector_id, "rest"):
        raise HTTPException(
            status_code=500, detail=f"Connector {connector_id!r} is not configured for REST"
        )

    try:
        tenant_id = resolve_tenant_id(
            headers=request.headers,
            jwt_identity=get_rest_caller_identity(request),
        )
    except MissingTenantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TenantIdentityMismatchError as exc:
        # Includes TenantRejectedError (reserved __default__, untrusted header).
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    name = resolve_config_name((config_name or "").strip() or None)
    if is_multitenancy_enabled():
        # Inline replace so CodeQL treats newline stripping as a sanitizer.
        logger.info(
            "Playground action | connector=%s | tenant_id=%s | config_name=%s",
            str(connector_id).replace("\r", " ").replace("\n", " "),
            str(tenant_id).replace("\r", " ").replace("\n", " "),
            str(name or "(default)").replace("\r", " ").replace("\n", " "),
        )
    try:
        return await factory.get(
            connector_id,
            tenant_id=tenant_id,
            config_name=name,
            action=action,
        )
    except ConfigNotFoundError:
        raise HTTPException(
            status_code=403,
            detail="No connector configuration for this tenant",
        )
    except ValueError as exc:
        # Incomplete auth blocks (e.g. service_account without sa_json_secret).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # The connector is configured but not loaded (not in NW_ALLOWED_CONNECTORS): say so, not "500".
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def get_fhir_connector(
    request: Request, config_name: Optional[str] = None
) -> FhirEpicConnector:
    connector = await resolve_connector(request, "fhir_epic", config_name=config_name)
    return connector  # type: ignore[return-value]


async def get_http_connector(request: Request, config_name: Optional[str] = None):
    connector = await resolve_connector(
        request, "http_generic", action="request", config_name=config_name
    )
    return connector


async def get_cerner_connector(request: Request, config_name: Optional[str] = None):
    connector = await resolve_connector(request, "fhir_cerner", config_name=config_name)
    return connector


@router.post("/report-incident", response_model=ScenarioResponse)
async def report_incident_scenario(
    payload: IncidentReportInput, connector: Any = Depends(get_http_connector)
) -> ScenarioResponse:
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []

    def add_step(
        name: str, status: str, details: str = "", display_name: str = "", data: Any = None
    ):
        steps.append(
            ScenarioStep(
                name=name, status=status, details=details, display_name=display_name, data=data
            )
        )

    # STEP 1: Format Payload
    add_step("Payload Formatting", "pending", display_name="Format Incident Payload")
    try:
        ts = datetime.now(tz=timezone.utc).isoformat()
        ticket_payload = {
            "ticket": {
                "subject": payload.title,
                "comment": {"body": payload.description},
                "priority": payload.severity.lower(),
                "custom_fields": [
                    {"id": 12345, "value": payload.component},
                    {"id": 67890, "value": ts},
                ],
                "requester": {"name": payload.reported_by},
            }
        }
        steps[-1].status = "success"
        steps[-1].details = "Standard ITSM schema generated."
        steps[-1].display_name = "Payload Ready"
        steps[-1].data = {"raw": ticket_payload}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 failed")

    # STEP 2: Dispatch Webhook
    add_step("Dispatch Webhook", "pending", display_name="Dispatch Webhook")
    try:
        from node_wire_http_generic.schema import HttpRequestInput

        # Using httpbin.org to simulate a real REST endpoint
        request_input = HttpRequestInput(
            url="https://httpbin.org/post",
            method="POST",
            headers={"X-Demo-Source": "node-wire"},
            body=ticket_payload,
        )

        http_action = connector
        response = await run_action(http_action, request_input)

        resp_body = json.loads(response.body)

        steps[-1].status = "success"
        steps[-1].details = f"HTTP {response.status_code} Success"
        steps[-1].display_name = "Webhook Dispatched"
        steps[-1].data = {"raw": resp_body}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 failed")

    # STEP 3: Verify & Visualize
    add_step("Verification", "pending", display_name="Verify Ticket Creation")
    try:
        # httpbin echoes back our data in 'json' field
        incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"

        beautiful_data = {
            "id": incident_id,
            "type": "IT Service Incident",
            "date": datetime.now().isoformat(),
            "status": "OPEN",
            "patient_name": payload.reported_by,
            "author": "AOT-Automator",
            "category": payload.component,
            "description": payload.title,
            "content_text": f"Incident documented and routed to Level 2 Support. Ref: {incident_id}\n\nDescription: {payload.description}",
        }

        steps[-1].status = "success"
        steps[-1].details = f"Incident {incident_id} Active"
        steps[-1].display_name = "Ticket Verified"
        steps[-1].data = {
            "raw": {"incident_id": incident_id, "upstream_status": "accepted"},
            "beautiful_data": beautiful_data,
        }
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 failed")

    # STEP 4: Audit Log
    add_step("Audit", "pending", display_name="Update Audit Log")
    try:
        # Simulate background task
        await asyncio.sleep(0.4)

        steps[-1].status = "success"
        steps[-1].details = "System Audit Recorded"
        steps[-1].display_name = "Audit Log Updated"

        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=incident_id,
            human_summary=f"IT Incident {incident_id} has been successfully created, routed, and audited.",
            trace_id=trace_id,
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 4 failed")


# ---------------------------------------------------------------------------
# AI Agent Chat endpoint
# ---------------------------------------------------------------------------


class AgentChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class AgentChatInput(BaseModel):
    message: str
    history: List[Dict[str, str]] = []  # [{"role": "user/assistant", "content": "..."}]
    llm_option: Optional[str] = None  # e.g. "groq/openai/gpt-oss-120b"
    llm_base_url: Optional[str] = None  # override for ollama / custom local entries


class AgentChatStepResponse(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Optional[str] = None


class AgentChatResponse(BaseModel):
    reply: str
    steps: List[AgentChatStepResponse] = []
    trace_id: str
    success: bool
    tenant_id: Optional[str] = None
    config_name: Optional[str] = None
    # Token totals for the whole turn; None when the backend reports no usage.
    usage: Optional[Dict[str, Any]] = None
    # Model that produced this turn, so the client can bucket totals per model.
    llm_option: Optional[str] = None


_TOOL_CALLING_FALLBACK_NOTE = (
    " This model may not support OpenAI-style tool calling. Switch back to Groq in the "
    "model selector and try again."
)


def _current_agent_transport() -> str:
    transport = os.environ.get("NW_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"
    return transport if transport in {"stdio", "streamable-http"} else "stdio"


def _build_agent_chat_task(payload: AgentChatInput) -> str:
    history_text_parts = []
    for msg in payload.history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history_text_parts.append(f"{role.upper()}: {content}")

    if history_text_parts:
        return (
            "Previous conversation:\n"
            + "\n".join(history_text_parts)
            + f"\n\nUSER (latest): {payload.message}"
        )
    return payload.message


def _augment_reply_for_tool_calling_errors(reply: str) -> str:
    from agents.llm_factory import looks_like_tool_calling_unsupported

    if looks_like_tool_calling_unsupported(reply) and "Switch back to Groq" not in reply:
        return reply.rstrip() + _TOOL_CALLING_FALLBACK_NOTE
    return reply


def _log_llm_selection(llm_option: Optional[str], trace_id: Optional[str] = None) -> str:
    """Log the active LLM option, calling out a change from the previous request.

    Returns the effective option id so callers can label per-model token totals.
    """
    global _last_llm_option
    from agents.llm_factory import DEFAULT_GROQ_MODEL

    effective = llm_option or f"groq/{os.environ.get('GROQ_MODEL', DEFAULT_GROQ_MODEL)}"
    previous = _last_llm_option
    _last_llm_option = effective
    # Inline replace so CodeQL treats newline stripping as a sanitizer.
    if previous and previous != effective:
        llm_logger.info(
            "LLM switched | trace_id=%s | from=%s | to=%s",
            trace_id or "-",
            previous.replace("\r", " ").replace("\n", " "),
            effective.replace("\r", " ").replace("\n", " "),
        )
    else:
        llm_logger.info(
            "LLM selected | trace_id=%s | option=%s",
            trace_id or "-",
            effective.replace("\r", " ").replace("\n", " "),
        )
    return effective


def _resolve_playground_llm_base_url(raw: Optional[str]) -> Optional[str]:
    """Validate and normalize optional playground ``llm_base_url``."""
    if not (raw or "").strip():
        return None
    from agents.llm_factory import normalize_openai_compatible_base_url

    try:
        return normalize_openai_compatible_base_url(raw or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent-transport")
async def agent_transport() -> Dict[str, str]:
    transport = _current_agent_transport()
    return {
        "transport": transport,
        "label": "Streamable HTTP" if transport == "streamable-http" else "stdio",
    }


@router.get("/llm-options")
async def llm_options() -> Dict[str, Any]:
    """Configured playground LLM options (provider/model ids) for the chat selector."""
    from agents.llm_factory import LLMProviderFactory

    return LLMProviderFactory.list_playground_options()


def _is_configured_ollama(origin: str) -> bool:
    """True when ``origin`` is the Ollama this server is configured for.

    That is ``OLLAMA_BASE_URL`` when set, else the built-in local default. It is one fixed,
    operator-side address, so it may be loopback or internal. Any other caller-supplied address
    must pass the outbound destination check.
    """
    from agents.llm_factory import DEFAULT_OLLAMA_BASE_URL, ollama_origin_from_base_url

    configured = os.environ.get("OLLAMA_BASE_URL", "").strip() or DEFAULT_OLLAMA_BASE_URL
    try:
        trusted = urlsplit(ollama_origin_from_base_url(configured))
    except ValueError:
        return False
    candidate = urlsplit(origin)
    return (trusted.scheme, trusted.netloc.lower()) == (candidate.scheme, candidate.netloc.lower())


@router.get("/llm-discover-ollama")
async def llm_discover_ollama(base_url: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    """Discover model tags from a running Ollama server.

    The URL is caller input and the model names are read back, so it is checked like any other
    outbound target unless it is the configured Ollama (``OLLAMA_BASE_URL``, default local).
    """
    import httpx

    from agents.llm_factory import normalize_openai_compatible_base_url, ollama_origin_from_base_url

    try:
        normalized_base = normalize_openai_compatible_base_url(base_url)
        origin = ollama_origin_from_base_url(normalized_base)
        tags_url = f"{origin.rstrip('/')}/api/tags"
    except ValueError:
        logger.warning("Ollama discover rejected invalid base_url")
        return {
            "models": [],
            "base_url": None,
            "error": "Invalid Ollama URL. Use http or https.",
        }

    try:
        if not _is_configured_ollama(origin):
            await assert_safe_destination(tags_url)
    except SsrfBlockedError:
        logger.warning("Ollama discover blocked an outbound destination")
        return {
            "models": [],
            "base_url": None,
            "error": (
                "Blocked destination. Only public hosts are allowed; "
                "set OLLAMA_BASE_URL on the server to use a private Ollama."
            ),
        }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(tags_url)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning("Ollama discover failed | url=%s", tags_url, exc_info=True)
        return {
            "models": [],
            "base_url": normalized_base,
            "error": "Could not reach Ollama. Check the base URL and that Ollama is running.",
        }

    models: List[str] = []
    for item in payload.get("models") or []:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            if name:
                models.append(name)
    models.sort()
    return {"models": models, "base_url": normalized_base, "error": None}


AGENT_GUARDRAIL_PROMPT = (
    "You are a healthcare data assistant. You have access to tools for fetching "
    "patient data from Cerner FHIR and Epic FHIR, uploading files to Google Drive, and sending "
    "emails via SMTP.\n"
    "Tool names are `<connector_id>_<action>` with dots in the action replaced by "
    "underscores (e.g. `fhir_cerner_read_patient`, `fhir_epic_read_patient`, "
    "`google_drive_drive_files_create`, `smtp_send_email`). "
    "Use exactly the names and JSON-schema arguments from tools/list.\n\n"
    "WORKFLOW (MUST EXECUTE SEQUENTIALLY, ONE STRICT STEP AT A TIME):\n"
    "When asked to 'Send patient summaries via email' or similar tasks, you MUST follow this exact flow in order. DO NOT parallelize these steps:\n"
    "  1. First turn: Obtain patient demographics from the EHR.\n"
    '     - If the user gave a Patient ID: call `fhir_cerner_read_patient` or `fhir_epic_read_patient` with JSON `{"resource_id": "<id>"}` (use Epic when the ID starts with \'e\'). Do NOT use search_patients for a known ID.\n'
    "     - If there is NO Patient ID but there IS a name: use name fields or `search_patients` per tools/list schema (e.g. `given_name`, `family_name`, `birthdate`, or valid `search_params`).\n"
    "     - Use `search_patients` only when you have no ID, or after `read_patient` failed and you need a fallback.\n"
    "     CRITICAL: If the user has NOT provided a patient ID or name in their message, you MUST ASK them for it. DO NOT call tools with a guessed or hallucinated ID like '12345'.\n"
    "  2. Second turn: Once you have the patient data from step 1, create a file on Google Drive containing the masked patient summary. Do NOT use placeholder content.\n"
    "  3. Third turn: Once step 2 returns a shareable Drive URL (see `data.raw.webViewLink` from tool `google_drive_drive_files_create`), send an email with that exact link. Do NOT call the email tool until you have the link.\n"
    "     CRITICAL: You MUST ask the user for the recipient email address if they haven't provided it. DO NOT guess email addresses like 'recipient_email@example.com'.\n"
    "     CRITICAL: In the email body, you MUST insert the actual URL string returned from step 2 (e.g. 'https://drive.google.com/...'). Do NOT literally write the text '<web_view_link>'.\n\n"
    "DATA PRIVACY & MASKING — follow these strictly:\n"
    "- Before uploading ANY data to Google Drive or sending it via Email, you MUST apply masking to the ACTUAL patient data you retrieved from tools:\n"
    "  - Date of Birth (DOB): Replace the year with '****' (e.g., if DOB is 1985-12-31, write ****-12-31).\n"
    "  - Patient ID: Mask all but the first 3 digits (e.g., if ID is '8877665', write '887****').\n"
    "  - NEVER use the placeholder values ('1990-05-12', '12724066', or 'Name') in your reports - always use the real patient data masked accordingly.\n"
    "- EMAIL WORKFLOW: When sending patient details to an email recipient:\n"
    "  1. ALWAYS upload the masked patient summary to Google Drive first.\n"
    "  2. Use `data.raw.webViewLink` from the `google_drive_drive_files_create` tool result.\n"
    "  3. In the email body, provide that link instead of the actual data.\n"
    "  4. The email body should be professional: 'Patient data summary from the EHR is available at the following secure link: [Link]'\n\n"
    "GUARDRAILS:\n"
    "- NEVER hallucinate or make up patient details. DO NOT guess IDs like '12345'. If missing, ask the user.\n"
    "- NEVER use placeholders like 'to be updated later' or '<web_view_link>'.\n"
    "- If a tool requires data from a previous tool's output, you MUST WAIT for the previous tool to complete in a previous turn.\n"
    "- If the user provides a Patient ID, do NOT ask for their name or birthdate. The ID is perfectly sufficient.\n"
    "- Do not call the same tool twice unless the first call failed.\n"
    "- Before calling any tool, verify you have ALL required parameters.\n"
    "- If a tool call fails, explain the error clearly and ask the user how to proceed.\n"
    "- Always confirm what you've done after completing the requested actions.\n"
    "- Write final answers in a clean ChatGPT/Gemini style: one short result sentence "
    "first, then a tight bullet list of what you did, then any links or notes. "
    "Use plain markdown (bullets, bold sparingly, full URLs). Do not dump tool JSON, "
    "do not narrate internal reasoning, and do not pad with filler.\n"
    "- Keep responses concise and professional.\n"
    "\n"
    "MULTI-TENANCY:\n"
    "- If the user names a tenant, call `nw_list_tenants` then `nw_select_tenant` "
    "with that `tenant_id`. That tenant applies to every connector on this server.\n"
    "- Then call `nw_select_config` with `config_name`. That name applies to every "
    "connector. If a connector has no config with that name, its tools return an error.\n"
    "- If the user does not name a tenant or config, use the default pin. "
    "Do not invent tenant ids or config names. Do not pass tenant_id or "
    "config_name on connector tools.\n"
)


def _build_agent_chat_task(payload: AgentChatInput) -> str:
    history_text_parts = []
    for msg in payload.history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history_text_parts.append(f"{role.upper()}: {content}")

    if history_text_parts:
        return (
            "Previous conversation:\n"
            + "\n".join(history_text_parts)
            + f"\n\nUSER (latest): {payload.message}"
        )
    return payload.message


def _current_agent_transport() -> str:
    transport = os.environ.get("NW_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"
    return transport if transport in {"stdio", "streamable-http"} else "stdio"


def _resolve_agent_mcp_tenant(request: Request) -> str:
    """Resolve the *starting* tenant pin for the playground agent → MCP.

    Unlike a direct connector call, the agent chat flow can pick a tenant
    itself mid-conversation via the `nw_select_tenant` chat tool (see
    AGENT_GUARDRAIL_PROMPT and `TenantSessionOverlay`), so a missing
    `X-Tenant-ID` here must not hard-fail the request the way
    `resolve_connector` does — that would make the dropdown the only way to
    ever start a chat. Mirrors the standalone MCP HTTP middleware's own
    fallback (`server.py`'s streamable-http tenant resolution): fall back to
    `__default__` when it's a configured tenant, else return "" so the agent
    starts unpinned and the LLM can call `nw_list_tenants` / `nw_select_tenant`
    itself for the first turn.

    Simplified: the `__default__` fallback applies only to an unauthenticated caller (local
    development). `__default__` is operator-only, so an authenticated caller who sends no tenant
    starts unpinned instead of inheriting the operator's tenant.
    """
    from bindings.rest_api.auth import get_rest_caller_identity

    identity = get_rest_caller_identity(request)
    try:
        return resolve_tenant_id(headers=request.headers, jwt_identity=identity)
    except TenantIdentityMismatchError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MissingTenantError:
        store = get_playground_factory().store
        if identity is None and DEFAULT_TENANT in store.list_tenants():
            return DEFAULT_TENANT
        return ""


def _agent_mcp_extra_headers(tenant_id: str) -> Dict[str, str]:
    from node_wire_runtime.identity import TENANT_HEADER

    # Empty tenant_id (no dropdown selection yet, no __default__ configured)
    # omits the header entirely rather than sending a blank X-Tenant-ID, so
    # the remote/proxy MCP server applies its own "no tenant pinned" chat
    # flow instead of failing tenant resolution outright.
    if not tenant_id:
        return {}
    # TENANT_HEADER is lowercased; HTTP clients accept any casing.
    return {TENANT_HEADER: tenant_id}


def _playground_inprocess_mcp_client(tenant_id: str, request: Request):
    """Share playground factory/store with agent tool calls (named tenants)."""
    from agents.toolhive import InProcessMcpClient
    from bindings.mcp_server.server import McpServer

    config_name = resolve_config_name(
        (request.query_params.get("config_name") or "").strip() or None
    )
    factory = get_playground_factory()
    server = McpServer(server_name="node-wire-playground-agent", factory=factory)
    return InProcessMcpClient(server, tenant_id=tenant_id, config_name=config_name)


def _tenancy_from_agent_steps(
    steps: Any, tenant_id: str, config_name: Optional[str]
) -> tuple[str, Optional[str]]:
    from bindings.mcp_server.server import SELECT_CONFIG_TOOL_ALIASES, SELECT_TENANT_TOOL_ALIASES

    def _field(step: Any, *names: str) -> Any:
        for name in names:
            if isinstance(step, dict) and name in step:
                return step[name]
            val = getattr(step, name, None)
            if val is not None:
                return val
        return None

    tenant, config = tenant_id, config_name
    for step in steps or []:
        tool = _field(step, "tool_called", "tool") or ""
        args = _field(step, "tool_args", "args") or {}
        if not isinstance(args, dict):
            args = {}
        if tool in SELECT_TENANT_TOOL_ALIASES:
            tid = args.get("tenant_id")
            if isinstance(tid, str) and tid.strip():
                tenant = tid.strip()
                config = None
        elif tool in SELECT_CONFIG_TOOL_ALIASES:
            name = args.get("config_name")
            if isinstance(name, str) and name.strip():
                config = name.strip()
    return tenant, config


def _tenancy_from_mcp_client(
    mcp_client: Any, tenant_id: str, config_name: Optional[str]
) -> tuple[str, Optional[str]]:
    server = getattr(mcp_client, "_server", None)
    if server is None:
        return tenant_id, config_name
    tenant_session = getattr(server, "tenant_session", None)
    selected_t = getattr(tenant_session, "selected_tenant_id", None)
    selected_c = getattr(tenant_session, "selected_config_name", None)
    if selected_t:
        return selected_t, selected_c
    return tenant_id, selected_c if selected_c is not None else config_name


def _agent_effective_tenancy(
    mcp_client: Any,
    steps: Any,
    tenant_id: str,
    config_name: Optional[str],
) -> tuple[str, Optional[str]]:
    from_steps = _tenancy_from_agent_steps(steps, tenant_id, config_name)
    if mcp_client is not None and getattr(mcp_client, "_server", None) is not None:
        return _tenancy_from_mcp_client(mcp_client, from_steps[0], from_steps[1])
    return from_steps


@router.post("/agent-chat", response_model=AgentChatResponse)
async def agent_chat(request: Request, payload: AgentChatInput) -> AgentChatResponse:
    """
    AI Agent chatbot endpoint.
    Accepts a user message + conversation history, runs through the ToolHiveAgent,
    and returns the agent's reply with any tool steps executed.
    """
    trace_id = str(uuid.uuid4())
    llm_option = (payload.llm_option or "").strip() or None
    # Inline replace so CodeQL treats newline stripping as a sanitizer.
    logger.info(
        "Agent Chat request | trace_id=%s | llm_option=%s",
        trace_id,
        str(llm_option or "groq(default)").replace("\r", " ").replace("\n", " "),
    )

    if not payload.message.strip():
        return AgentChatResponse(
            reply="Please type a message to get started.",
            steps=[],
            trace_id=trace_id,
            success=False,
        )

    tenant_id = _resolve_agent_mcp_tenant(request)
    mcp_headers = _agent_mcp_extra_headers(tenant_id)
    config_name = resolve_config_name(
        (request.query_params.get("config_name") or "").strip() or None
    )
    if is_multitenancy_enabled():
        logger.info(
            "Agent Chat | mcp_tenant_id=%s | config_name=%s",
            str(tenant_id or "(none)").replace("\r", " ").replace("\n", " "),
            str(config_name or "(default)").replace("\r", " ").replace("\n", " "),
        )

    try:
        from agents.llm_factory import LLMProviderFactory
        from agents.toolhive import (
            MultiMcpClient,
            ToolHiveAgent,
            resolve_mcp_urls,
            resolve_max_tool_failures,
        )
        from node_wire_runtime.mcp_client.client import create_http_mcp_client

        # Inline replace so CodeQL treats newline stripping as a sanitizer.
        logger.info(
            "Agent Chat | creating LLM provider from option: %s",
            str(llm_option or "(default groq)").replace("\r", " ").replace("\n", " "),
        )
        effective_llm_option = _log_llm_selection(llm_option, trace_id)
        llm_base_url = _resolve_playground_llm_base_url(payload.llm_base_url)
        llm_provider = LLMProviderFactory.create_from_option(llm_option, base_url=llm_base_url)

        task = _build_agent_chat_task(payload)

        # Determine MCP transport — try proxy first, fallback to local in-process
        transport = _current_agent_transport()
        urls = resolve_mcp_urls() if transport == "streamable-http" else []
        run_result = None
        mcp_used = None
        fallback_to_stdio = (
            os.environ.get("PLAYGROUND_AGENT_PROXY_FALLBACK_TO_STDIO", "true").lower() == "true"
        )

        if urls:
            logger.info("Agent Chat | trying ToolHive proxy URL(s): %s", ",".join(urls))
            try:
                if len(urls) == 1:
                    mcp_client = create_http_mcp_client(urls[0], extra_headers=mcp_headers)
                else:
                    mcp_client = MultiMcpClient(
                        [create_http_mcp_client(u, extra_headers=mcp_headers) for u in urls]
                    )
                agent = ToolHiveAgent(
                    mcp_client,
                    llm_provider,
                    max_steps=10,
                    max_tool_failures=resolve_max_tool_failures(None),
                )
                agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                run_result = await agent.run(task)
                mcp_used = mcp_client
                # Fallback to local stdio if:
                # (a) agent hard-failed due to missing tools, OR
                # (b) agent "succeeded" but called zero tools (LLM gave up because
                #     only a subset of tools was discoverable via the proxy)
                proxy_incomplete = (
                    not run_result.success
                    and run_result.error
                    and (
                        "Failed to list MCP tools" in run_result.error
                        or "not in request.tools" in run_result.error
                    )
                )
                if proxy_incomplete:
                    if fallback_to_stdio:
                        logger.warning("Agent Chat | proxy incomplete, falling back to local stdio")
                        run_result = None
                        mcp_used = None
                    else:
                        logger.warning(
                            "Agent Chat | proxy incomplete, returning proxy error to UI "
                            "(set PLAYGROUND_AGENT_PROXY_FALLBACK_TO_STDIO=true to fallback)"
                        )
            except Exception as proxy_err:
                if fallback_to_stdio:
                    logger.warning(
                        "Agent Chat | proxy error: %s — falling back to local stdio", proxy_err
                    )
                    run_result = None
                    mcp_used = None
                else:
                    logger.warning(
                        "Agent Chat | proxy error: %s — returning error to UI "
                        "(set PLAYGROUND_AGENT_PROXY_FALLBACK_TO_STDIO=true to fallback)",
                        proxy_err,
                    )
                    return AgentChatResponse(
                        reply=f"MCP proxy error: {proxy_err}",
                        steps=[],
                        trace_id=trace_id,
                        success=False,
                    )

        if run_result is None:
            # In-process MCP shares the playground config store (named tenants).
            logger.info("Agent Chat | using in-process MCP (shared factory)")
            async with _playground_inprocess_mcp_client(tenant_id, request) as mcp_client:
                agent = ToolHiveAgent(
                    mcp_client,
                    llm_provider,
                    max_steps=10,
                    max_tool_failures=resolve_max_tool_failures(None),
                )
                agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                run_result = await agent.run(task)
                mcp_used = mcp_client

        # Map agent steps to response format
        chat_steps = []
        for s in run_result.steps:
            chat_steps.append(
                AgentChatStepResponse(
                    tool=s.tool_called or "unknown",
                    args=s.tool_args,
                    result=s.tool_result,
                )
            )

        reply = (
            run_result.final_answer
            or run_result.error
            or "I encountered an issue. Please try again."
        )
        reply = _augment_reply_for_tool_calling_errors(reply)

        eff_tenant, eff_config = _agent_effective_tenancy(
            mcp_used, run_result.steps, tenant_id, config_name
        )
        if is_multitenancy_enabled():
            logger.info(
                "Agent Chat | effective tenant_id=%s | config_name=%s",
                str(eff_tenant or "(none)").replace("\r", " ").replace("\n", " "),
                str(eff_config or "(default)").replace("\r", " ").replace("\n", " "),
            )

        from agents.toolhive import _usage_as_dict

        return AgentChatResponse(
            reply=reply,
            steps=chat_steps,
            trace_id=trace_id,
            success=run_result.success,
            tenant_id=eff_tenant,
            config_name=eff_config,
            usage=_usage_as_dict(run_result.usage, run_result.steps_used),
            llm_option=effective_llm_option,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Agent Chat failed: %s", e, exc_info=True)
        reply = (
            f"Sorry, I encountered an error: {str(e)}. "
            "Please check the server configuration and try again."
        )
        return AgentChatResponse(
            reply=_augment_reply_for_tool_calling_errors(reply),
            steps=[],
            trace_id=trace_id,
            success=False,
        )


@router.post("/agent-chat-stream")
async def agent_chat_stream(request: Request, payload: AgentChatInput) -> Any:
    """
    Stream agent progress and final-answer chunks to web clients.

    The terminal ``done`` event includes ``trace_id`` and ``message``. Clients
    should stop their streaming loader only when that event arrives.
    """
    tenant_id = _resolve_agent_mcp_tenant(request)
    mcp_headers = _agent_mcp_extra_headers(tenant_id)
    config_name = resolve_config_name(
        (request.query_params.get("config_name") or "").strip() or None
    )
    if is_multitenancy_enabled():
        logger.info(
            "Agent Chat stream | mcp_tenant_id=%s | config_name=%s",
            str(tenant_id or "(none)").replace("\r", " ").replace("\n", " "),
            str(config_name or "(default)").replace("\r", " ").replace("\n", " "),
        )

    async def stream_events():
        try:
            from agents.llm_factory import LLMProviderFactory
            from agents.toolhive import (
                MultiMcpClient,
                ToolHiveAgent,
                resolve_mcp_urls,
                resolve_max_tool_failures,
            )
            from node_wire_runtime.mcp_client.client import create_http_mcp_client

            if not payload.message.strip():
                trace_id = str(uuid.uuid4())
                yield (
                    json.dumps(
                        {
                            "type": "final_chunk",
                            "content": "Please type a message to get started.",
                        }
                    )
                    + "\n"
                )
                yield (
                    json.dumps(
                        {
                            "type": "done",
                            "trace_id": trace_id,
                            "success": False,
                            "message": f"Streaming failed. trace_id={trace_id}",
                        }
                    )
                    + "\n"
                )
                return

            llm_option = (payload.llm_option or "").strip() or None
            # Inline replace so CodeQL treats newline stripping as a sanitizer.
            logger.info(
                "Agent Chat stream | creating LLM provider from option: %s",
                str(llm_option or "(default groq)").replace("\r", " ").replace("\n", " "),
            )
            effective_llm_option = _log_llm_selection(llm_option)
            llm_base_url = _resolve_playground_llm_base_url(payload.llm_base_url)
            llm_provider = LLMProviderFactory.create_from_option(llm_option, base_url=llm_base_url)
            task = _build_agent_chat_task(payload)
            transport = _current_agent_transport()
            urls = resolve_mcp_urls() if transport == "streamable-http" else []
            # Default true: playground should work without a separate MCP on :8081.
            fallback_to_local = (
                os.environ.get("PLAYGROUND_AGENT_PROXY_FALLBACK_TO_STDIO", "true").lower() == "true"
            )
            step_tenant, step_config = tenant_id, config_name

            if urls:
                proxy_ok = False
                try:
                    if len(urls) == 1:
                        mcp_client = create_http_mcp_client(urls[0], extra_headers=mcp_headers)
                    else:
                        mcp_client = MultiMcpClient(
                            [create_http_mcp_client(u, extra_headers=mcp_headers) for u in urls]
                        )
                    agent = ToolHiveAgent(
                        mcp_client,
                        llm_provider,
                        max_steps=10,
                        max_tool_failures=resolve_max_tool_failures(None),
                    )
                    agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                    async for event in agent.run_events(task):
                        if event.get("type") == "step":
                            step_tenant, step_config = _tenancy_from_agent_steps(
                                [event], step_tenant, step_config
                            )
                        if event.get("type") == "done":
                            event = dict(event)
                            event["tenant_id"] = step_tenant
                            event["config_name"] = step_config
                            event["llm_option"] = effective_llm_option
                        if (
                            fallback_to_local
                            and event.get("type") == "error"
                            and "Failed to list MCP tools" in str(event.get("message") or "")
                        ):
                            logger.warning(
                                "Agent Chat stream | proxy incomplete (%s) — "
                                "falling back to in-process MCP",
                                event.get("message"),
                            )
                            break
                        proxy_ok = True
                        yield json.dumps(event) + "\n"
                    else:
                        # Exhausted generator without break → proxy finished normally.
                        return
                    if proxy_ok:
                        return
                except Exception as proxy_err:
                    if not fallback_to_local:
                        raise
                    logger.warning(
                        "Agent Chat stream | proxy error: %s — falling back to in-process MCP",
                        proxy_err,
                    )

            async with _playground_inprocess_mcp_client(tenant_id, request) as mcp_client:
                agent = ToolHiveAgent(
                    mcp_client,
                    llm_provider,
                    max_steps=10,
                    max_tool_failures=resolve_max_tool_failures(None),
                )
                agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                async for event in agent.run_events(task):
                    if event.get("type") == "step":
                        step_tenant, step_config = _tenancy_from_agent_steps(
                            [event], step_tenant, step_config
                        )
                    if event.get("type") == "done":
                        event = dict(event)
                        eff_tenant, eff_config = _tenancy_from_mcp_client(
                            mcp_client, step_tenant, step_config
                        )
                        event["tenant_id"] = eff_tenant
                        event["config_name"] = eff_config
                        event["llm_option"] = effective_llm_option
                    yield json.dumps(event) + "\n"

        except HTTPException:
            raise
        except Exception as exc:
            trace_id = str(uuid.uuid4())
            logger.error("Agent Chat stream failed (trace_id=%s): %s", trace_id, exc, exc_info=True)
            yield (
                json.dumps(
                    {
                        "type": "final_chunk",
                        "content": _augment_reply_for_tool_calling_errors(
                            "Sorry, I encountered an internal error. "
                            f"Please check the server configuration and try again. trace_id={trace_id}"
                        ),
                    }
                )
                + "\n"
            )
            yield (
                json.dumps(
                    {
                        "type": "done",
                        "trace_id": trace_id,
                        "success": False,
                        "message": f"Streaming failed. trace_id={trace_id}",
                    }
                )
                + "\n"
            )

    return StreamingResponse(stream_events(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# External Patient Viewer — Read-Only Retrieval
# ---------------------------------------------------------------------------


def _get_viewer_connector(source_system: str) -> Any:
    """Return the correct FHIR connector based on source_system string."""
    if source_system.lower() == "cerner":
        return get_cerner_connector()
    return get_fhir_connector()


@router.post("/external-patient-viewer", response_model=ScenarioResponse)
async def external_patient_viewer_scenario(
    payload: ExternalPatientViewerInput,
) -> ScenarioResponse:
    """
    4-step read-only workflow: resolve patient identity, retrieve demographics,
    retrieve encounter history, retrieve document metadata.

    No FHIR resource is created or mutated during this workflow.
    Encounter-as-document fallback is applied when document_references are absent.
    """
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []
    is_epic = payload.source_system.lower() != "cerner"

    def add_step(
        name: str, status: str, details: str = "", display_name: str = "", data: Any = None
    ):
        steps.append(
            ScenarioStep(
                name=name, status=status, details=details, display_name=display_name, data=data
            )
        )

    connector = _get_viewer_connector(payload.source_system)
    system_label = "Epic FHIR R4" if is_epic else "Cerner FHIR R4"

    # ── STEP 1: Patient Resolution ──────────────────────────────────────────
    add_step("Patient Resolution", "pending", display_name="Resolve Patient Identity")
    try:
        if payload.patient_id:
            logger.info(
                "[ExtViewer] Direct Patient ID lookup on %s",
                system_label,
                extra={"trace_id": trace_id},
            )
            if is_epic:
                p_res = await run_action(
                    connector, FhirPatientReadInput(resource_id=payload.patient_id)
                )
            else:
                p_res = await run_action(
                    connector, FhirCernerPatientReadInput(resource_id=payload.patient_id)
                )
            patient_id = payload.patient_id
            patient_resource = p_res.resource or {}
        else:
            # Identity-layer search: resolve via name + birthdate
            if not (payload.patient_family or payload.patient_given):
                raise ValueError(
                    "Provide either patient_id or at least one name field (given/family) "
                    "to resolve patient identity."
                )
            search_params = {
                k: v
                for k, v in {
                    "family": payload.patient_family,
                    "given": payload.patient_given,
                    "birthdate": payload.patient_birthdate,
                }.items()
                if v
            }
            # Log only literal field names (not the payload-derived dict) so no
            # user-controlled data reaches the log record.
            provided_fields = {
                "family": bool(payload.patient_family),
                "given": bool(payload.patient_given),
                "birthdate": bool(payload.patient_birthdate),
            }
            logger.info(
                "[ExtViewer] Identity-layer search by fields [%s] on %s",
                ", ".join(sorted(k for k, present in provided_fields.items() if present)),
                system_label,
                extra={"trace_id": trace_id},
            )
            if is_epic:
                p_res = await run_action(
                    connector, FhirPatientReadInput(search_params=search_params)
                )
            else:
                p_res = await run_action(
                    connector, FhirCernerPatientReadInput(search_params=search_params)
                )
            patient_resource = p_res.resource or {}
            patient_id = patient_resource.get("id")

        if not patient_id:
            raise ValueError("Patient could not be resolved. No matching record found.")

        # Extract display name from FHIR resource
        name_obj = patient_resource.get("name", [{}])
        if name_obj and isinstance(name_obj, list):
            official = next((n for n in name_obj if n.get("use") == "official"), name_obj[0])
        else:
            official = {}
        given_parts = official.get("given", [])
        family_part = official.get("family", "")
        patient_display = f"{' '.join(given_parts)} {family_part}".strip() or (
            f"{payload.patient_given or ''} {payload.patient_family or ''}".strip() or patient_id
        )
        patient_dob = patient_resource.get("birthDate", "Unknown")
        patient_gender = patient_resource.get("gender", "Unknown")

        steps[-1].status = "success"
        steps[-1].details = f"Resolved: {patient_display} (ID: {patient_id})"
        steps[-1].display_name = f"Identity Resolved: {patient_display}"
        steps[-1].data = {
            "patient_id": patient_id,
            "display_name": patient_display,
            "dob": patient_dob,
            "gender": patient_gender,
            "source_system": system_label,
            "raw": patient_resource,
        }
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 — Patient Resolution failed")

    # ── STEP 2: Encounter History ────────────────────────────────────────────
    add_step("Encounter History", "pending", display_name="Retrieve Encounter History")
    encounters: List[Any] = []
    try:
        max_enc = max(1, min(20, payload.max_encounters))

        if is_epic:
            enc_res = await run_action(
                connector,
                FhirEncounterSearchInput(
                    search_params={"patient": patient_id, "_count": str(max_enc)}
                ),
            )
        else:
            enc_res = await run_action(
                connector,
                FhirCernerEncounterSearchInput(
                    search_params={"patient": patient_id, "_count": str(max_enc)}
                ),
            )

        encounters = enc_res.resources or []
        enc_count = len(encounters)

        most_recent_enc: dict = {}
        if encounters:
            most_recent_enc = encounters[0]
        recent_enc_type = (
            (most_recent_enc.get("type") or [{}])[0].get("text", "Encounter")
            if most_recent_enc
            else "None"
        )
        recent_enc_date = (
            most_recent_enc.get("period", {}).get("start", "Unknown date")
            if most_recent_enc
            else "N/A"
        )

        steps[-1].status = "success"
        steps[-1].details = (
            f"Retrieved {enc_count} encounter(s). "
            f"Most recent: {recent_enc_type} on {recent_enc_date}"
            if enc_count
            else "No encounters found for this patient."
        )
        steps[-1].display_name = (
            f"Encounter History: {enc_count} record(s)" if enc_count else "No Encounters Found"
        )
        steps[-1].data = {
            "encounter_count": enc_count,
            "encounters": encounters,
            "raw": {"total": enc_count, "entries": encounters},
        }
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 — Encounter Retrieval failed")

    # ── STEP 3: Document Metadata ────────────────────────────────────────────
    add_step("Document Metadata", "pending", display_name="Retrieve Document Metadata")
    documents: List[Any] = []
    doc_source = "fhir"
    try:
        max_docs = max(1, min(50, payload.max_documents))

        if is_epic:
            doc_res = await run_action(
                connector,
                FhirDocumentReferenceSearchInput(
                    search_params={"patient": patient_id, "_count": str(max_docs)}
                ),
            )
        else:
            doc_res = await run_action(
                connector,
                FhirCernerDocumentReferenceSearchInput(
                    search_params={"patient": patient_id, "_count": str(max_docs)}
                ),
            )

        documents = doc_res.resources or []

        # Encounter-as-document fallback: when no DocumentReference exists,
        # synthesise a lightweight document record from each encounter entry.
        if not documents and encounters:
            doc_source = "encounter_fallback"
            for enc in encounters[:max_docs]:
                enc_id = enc.get("id", "unknown")
                enc_type_text = (enc.get("type") or [{}])[0].get("text", "Clinical Encounter")
                enc_date = enc.get("period", {}).get("start", "Unknown")
                enc_status = enc.get("status", "unknown")
                documents.append(
                    {
                        "id": f"ENC-{enc_id}",
                        "resourceType": "EncounterFallback",
                        "status": enc_status,
                        "type": {"text": enc_type_text},
                        "date": enc_date,
                        "description": "Encounter summary (no DocumentReference found)",
                        "subject": {"reference": f"Patient/{patient_id}"},
                        "_synthetic": True,
                    }
                )
            logger.info(
                "[ExtViewer] No DocumentReferences found; using %d encounter fallback record(s)",
                len(documents),
                extra={"trace_id": trace_id},
            )

        doc_count = len(documents)
        fallback_note = " (encounter-fallback)" if doc_source == "encounter_fallback" else ""

        steps[-1].status = "success"
        steps[-1].details = (
            f"Retrieved {doc_count} document(s){fallback_note}."
            if doc_count
            else "No documents or encounters available for this patient."
        )
        steps[-1].display_name = (
            f"Documents: {doc_count} record(s){fallback_note}"
            if doc_count
            else "No Documents Found"
        )
        steps[-1].data = {
            "document_count": doc_count,
            "source": doc_source,
            "documents": documents,
            "raw": {"total": doc_count, "source": doc_source, "entries": documents},
        }
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 — Document Retrieval failed")

    # ── STEP 4: Viewer Assembly ──────────────────────────────────────────────
    add_step("Chart Assembly", "pending", display_name="Assemble External Chart View")
    try:
        enc_lines = []
        for i, enc in enumerate(encounters[:5]):
            enc_type = (enc.get("type") or [{}])[0].get("text", "Encounter")
            enc_date = enc.get("period", {}).get("start", "Unknown")
            enc_status = enc.get("status", "?")
            enc_lines.append(f"  [{i + 1}] {enc_type} | {enc_date} | Status: {enc_status}")

        doc_lines = []
        for i, doc in enumerate(documents[:5]):
            d_type = doc.get("type", {}).get("text") or doc.get("description", "Document")
            d_date = doc.get("date") or doc.get("period", {}).get("start", "Unknown")
            d_status = doc.get("status", "?")
            d_synth = " [enc-fallback]" if doc.get("_synthetic") else ""
            doc_lines.append(f"  [{i + 1}] {d_type}{d_synth} | {d_date} | Status: {d_status}")

        content_lines = (
            [
                f"=== External Patient Chart ({system_label}) ===",
                f"Patient   : {patient_display}",
                f"FHIR ID   : {patient_id}",
                f"DOB       : {patient_dob}",
                f"Gender    : {patient_gender}",
                "",
                f"--- Encounter History ({len(encounters)} record(s)) ---",
            ]
            + (enc_lines if enc_lines else ["  No encounters found."])
            + [
                "",
                f"--- Documents ({len(documents)} record(s)"
                + (" — encounter fallback" if doc_source == "encounter_fallback" else "")
                + ") ---",
            ]
            + (doc_lines if doc_lines else ["  No documents found."])
            + [
                "",
                "[READ-ONLY] No data was written to the source system.",
            ]
        )

        beautiful_data = {
            "id": f"CHART-{patient_id}",
            "type": "External Patient Chart",
            "date": datetime.now(tz=timezone.utc).isoformat(),
            "status": "READ-ONLY",
            "patient_name": patient_display,
            "author": system_label,
            "category": "Clinical Chart View",
            "description": (
                f"{len(encounters)} Encounter(s) · "
                f"{len(documents)} Document(s)"
                + (" [enc-fallback]" if doc_source == "encounter_fallback" else "")
            ),
            "content_text": "\n".join(content_lines),
        }

        steps[-1].status = "success"
        steps[-1].details = (
            f"Chart assembled. {len(encounters)} encounter(s), "
            f"{len(documents)} document(s). Read-only — 0 writes."
        )
        steps[-1].display_name = "Chart Ready (Read-Only)"
        steps[-1].data = {
            "patient_id": patient_id,
            "encounter_count": len(encounters),
            "document_count": len(documents),
            "document_source": doc_source,
            "read_only": True,
            "raw": {
                "patient_id": patient_id,
                "source_system": system_label,
                "encounters": len(encounters),
                "documents": len(documents),
                "document_source": doc_source,
            },
            "beautiful_data": beautiful_data,
        }

        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=patient_id,
            human_summary=(
                f"External chart loaded for {patient_display} from {system_label}. "
                f"{len(encounters)} encounter(s) and {len(documents)} document(s) retrieved. "
                "No data was written to the source system."
            ),
            trace_id=trace_id,
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 4 — Chart Assembly failed")

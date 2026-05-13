from __future__ import annotations

import logging
from typing import Any, Dict

import os
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Production: set NW_REST_LOAD_DOTENV=false to rely on injected env only (no .env file).
if os.environ.get("NW_REST_LOAD_DOTENV", "true").lower() not in ("0", "false", "no"):
    # Override inherited shell env so local .env edits are honored consistently.
    load_dotenv(override=True)

from bindings.factory import ConnectorFactory
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import build_manifest
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.ingress import enforce_authoritative_action, normalize_mcp_tool_arguments
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from node_wire_runtime.rate_limit import global_rate_limiter, RateLimitExceeded

from bindings.rest_api.auth import RestAuthMiddleware, get_rest_caller_identity

# Add project root to sys.path to allow importing from 'playground' package
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
    
from playground.scenarios import router as scenarios_router




logger = logging.getLogger("bindings.rest_api")
tracer = trace.get_tracer("bindings.rest_api")

app = FastAPI(title="Node Wire - REST API")
FastAPIInstrumentor.instrument_app(app)
# Auth runs outermost (added last): protects /connectors/*, /playground/*, /scenarios/*; /health is public.
app.add_middleware(RestAuthMiddleware)

# Include the professional scenarios orchestrator
app.include_router(scenarios_router)

# Serve the playground UI - use absolute path
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEMO_DIR = BASE_DIR / "playground"
app.mount("/playground", StaticFiles(directory=str(DEMO_DIR), html=True), name="playground")

_factory: ConnectorFactory | None = None


def get_factory() -> ConnectorFactory:
    global _factory
    if _factory is None:
        _factory = ConnectorFactory()
        auto_register()
        _factory.load()
    return _factory


async def check_rate_limit() -> None:
    try:
        # Skip rate limiting if disabled
        if os.environ.get("NW_RATE_LIMIT_DISABLED", "false").lower() not in ("true", "1", "yes"):
            await global_rate_limiter.acquire()
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc))


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"status": "ok"}


def _http_status_for_category(category: ErrorCategory | None) -> int:
    if category is None:
        return 200
    if category is ErrorCategory.BUSINESS:
        return 400
    if category is ErrorCategory.AUTH:
        return 401
    if category is ErrorCategory.RETRYABLE:
        return 503
    return 500


def _make_endpoint(cid: str, act: str) -> Any:
    async def endpoint(
        request: Request,
        payload: Dict[str, Any],
        factory_dep: ConnectorFactory = Depends(get_factory),
        _: None = Depends(check_rate_limit),
    ) -> JSONResponse:
        """
        Concrete endpoint for a specific connector/action, e.g.
        POST /connectors/http_generic/request
        """
        span = trace.get_current_span()
        span.set_attribute("connector.id", cid)
        span.set_attribute("connector.action", act)

        connector = factory_dep.get_for_protocol(cid, "rest", action=act)
        if connector is None:
            raise HTTPException(status_code=404, detail="Connector not available for REST")
        run_payload = dict(payload)
        run_payload = normalize_mcp_tool_arguments(connector, act, run_payload)
        try:
            enforce_authoritative_action(run_payload, act)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        run_payload["action"] = act
        # Let the runtime (Layer A) perform full schema validation.
        # Any validation errors will be mapped into ConnectorResponse.
        rest_id = get_rest_caller_identity(request)
        response: ConnectorResponse = await connector.run(
            run_payload,
            principal=rest_id.principal if rest_id else None,
            tenant_id=rest_id.tenant_id if rest_id else None,
            scopes=rest_id.scopes if rest_id else None,
        )
        status = _http_status_for_category(response.error_category)

        if not response.success:
            span.set_status(Status(StatusCode.ERROR))
            if response.error_category is not None:
                span.set_attribute("aot.error.category", response.error_category.value)
            if response.error_code is not None:
                span.set_attribute("aot.error.code", response.error_code)

        return JSONResponse(
            status_code=status,
            content=response.model_dump(),
        )

    return endpoint


def _build_dynamic_routes() -> None:
    factory = get_factory()

    connectors = factory.list_for_protocol("rest")
    manifest = build_manifest(connectors)

    for entry in manifest:
        connector_id = entry["connector_id"]
        action = entry["action"]

        # For REST, let the runtime perform full Pydantic validation.
        # We accept an arbitrary JSON object as the payload and forward it
        # directly to connector.run(...).
        route_path = f"/connectors/{connector_id}/{action}"
        app.post(route_path, name=f"{connector_id}_{action}")(_make_endpoint(connector_id, action))


_build_dynamic_routes()

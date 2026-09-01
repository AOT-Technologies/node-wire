#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
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
    # Do not override existing os.environ keys (pytest/conftest injects values first).
    load_dotenv(override=False)

from bindings.factory import ConnectorFactory
from bindings.invoke import ConnectorNotExposed, invoke
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import build_manifest
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.config_store import (
    ConfigNameConflictError,
    ConfigNotFoundError,
    ConfigStoreError,
    DefaultDeletionError,
)
from node_wire_runtime.identity import (
    MissingTenantError,
    is_multitenancy_enabled,
    resolve_config_name,
    resolve_tenant_id,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from node_wire_runtime.rate_limit import global_rate_limiter, RateLimitExceeded

from bindings.rest_api.rate_limit import InMemoryRateLimiter
from bindings.rest_api.auth import (
    RestAuthMiddleware,
    get_request_identity_key,
    get_rest_caller_identity,
)
from bindings.rest_api.body_limit import MaxBodySizeMiddleware

logger = logging.getLogger("bindings.rest_api")
tracer = trace.get_tracer("bindings.rest_api")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _playground_enabled() -> bool:
    """Return True when demo playground routes/static files should be mounted."""
    raw = os.environ.get("NW_REST_PLAYGROUND_ENABLED")
    if raw is not None and raw.strip():
        return _truthy_env(raw)
    return (_REPO_ROOT / "playground").is_dir()


def _mount_playground(app: FastAPI) -> None:
    """Attach scenario API routes and static UI when playground is available."""
    if not _playground_enabled():
        logger.info(
            "REST playground disabled",
            extra={"reason": "NW_REST_PLAYGROUND_ENABLED=false or playground/ missing"},
        )
        return

    playground_dir = _REPO_ROOT / "playground"
    if str(_REPO_ROOT) not in sys.path:
        sys.path.append(str(_REPO_ROOT))

    try:
        from playground.scenarios import router as scenarios_router  # noqa: E402
    except ImportError as exc:
        logger.warning(
            "Playground directory present but scenarios module could not be imported; skipping",
            extra={"error": str(exc)},
        )
        return

    app.include_router(scenarios_router)

    # Playground feature-flags endpoint: lets the UI query server-side config.
    from node_wire_runtime.identity import is_multitenancy_enabled

    @app.get("/playground/feature-flags", tags=["playground"], include_in_schema=False)
    async def playground_feature_flags() -> Dict[str, Any]:
        return {"multitenancy_enabled": is_multitenancy_enabled()}

    app.mount(
        "/playground",
        StaticFiles(directory=str(playground_dir), html=True),
        name="playground",
    )
    logger.info("REST playground mounted at /playground")


app = FastAPI(title="Node Wire - REST API")
FastAPIInstrumentor.instrument_app(app)
_max_body_bytes = int(os.environ.get("NW_REST_MAX_BODY_BYTES", "10485760"))
# Body limit runs outermost (added last); auth is next; protects /connectors/* and /scenarios/*.
app.add_middleware(RestAuthMiddleware)
app.add_middleware(MaxBodySizeMiddleware, max_body_bytes=_max_body_bytes)

_mount_playground(app)

_factory: ConnectorFactory | None = None
_rate_limiter: InMemoryRateLimiter | None = None
_rate_limiter_cfg: tuple[int, int, int, int] | None = None


def get_factory() -> ConnectorFactory:
    global _factory
    if _factory is None:
        _factory = ConnectorFactory()
        auto_register()
        _factory.load()
        from node_wire_runtime.tenant_persistence import load_tenants

        load_tenants(_factory.store)
    return _factory


def _persist_tenants(factory_dep: ConnectorFactory) -> None:
    from node_wire_runtime.tenant_persistence import save_tenants

    save_tenants(factory_dep.store)


def _pop_secrets(doc: Dict[str, Any]) -> Dict[str, str] | None:
    """Extract secrets from a config body.

    Returns ``None`` when the client omitted ``secrets`` (config-only update).
    Returns a (possibly empty) dict when ``secrets`` was present — empty means
    validate required secrets against existing values for that config only.
    """
    if "secrets" not in doc:
        return None
    raw = doc.pop("secrets")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="secrets must be a JSON object")
    return {str(k): str(v) for k, v in raw.items() if v is not None and str(v).strip()}


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


@app.get("/ready", tags=["system"])
async def ready() -> Dict[str, str]:
    try:
        factory = get_factory()
        if not factory.list_for_protocol("rest") and not factory.list_for_protocol("grpc"):
            raise HTTPException(status_code=503, detail="no connectors loaded")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="factory not ready")
    return {"status": "ready"}


# --- Runtime connector config API (thin wrappers over ConnectorConfigStore) ---
# Tenant scope comes from the tenant header (never the path). The embedding
# application authenticates callers before these endpoints are reachable
# (RestAuthMiddleware); node-wire does not.


def _config_tenant(request: Request) -> str:
    try:
        return resolve_tenant_id(
            headers=request.headers, jwt_identity=get_rest_caller_identity(request)
        )
    except MissingTenantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _log_tenant_action(
    *,
    action: str,
    tenant_id: str,
    connector_id: str | None = None,
    config_name: str | None = None,
) -> None:
    """Emit tenant context when multitenancy is on (visible in default formatters)."""
    if not is_multitenancy_enabled():
        return
    # Inline replace so CodeQL treats newline stripping as a sanitizer.
    logger.info(
        "Tenant config | op=%s | tenant_id=%s | connector=%s | config_name=%s",
        str(action).replace("\r", " ").replace("\n", " "),
        str(tenant_id).replace("\r", " ").replace("\n", " "),
        str(connector_id or "-").replace("\r", " ").replace("\n", " "),
        str(config_name or "(default)").replace("\r", " ").replace("\n", " "),
    )


def _map_config_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MissingTenantError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ConfigNameConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DefaultDeletionError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ConfigNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConfigStoreError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@app.post("/v1/config/init", tags=["config"])
async def config_init(
    request: Request,
    payload: Dict[str, Any],
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    """Bulk init. With the tenant header: payload is that tenant's
    ``{connector_id: [docs]}`` map. Without it: full multi-tenant payload."""
    from node_wire_runtime.identity import tenant_from_headers

    header_tenant = tenant_from_headers(request.headers)
    full_payload = {header_tenant: payload} if header_tenant else payload
    try:
        factory_dep.store.init(full_payload)
        _persist_tenants(factory_dep)
    except Exception as exc:  # noqa: BLE001
        raise _map_config_error(exc)
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.get("/v1/tenants", tags=["config"])
async def list_tenants(
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    return JSONResponse(status_code=200, content={"tenants": factory_dep.store.list_tenants()})


@app.put("/v1/connectors/{cid}/secrets", tags=["config"])
async def secrets_upsert(
    cid: str,
    request: Request,
    payload: Dict[str, Any],
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    from node_wire_runtime.tenant_persistence import upsert_tenant_secrets

    tenant_id = _config_tenant(request)
    secrets = payload.get("secrets")
    if not isinstance(secrets, dict):
        raise HTTPException(status_code=400, detail="body.secrets must be a JSON object")
    config_name = str(payload.get("config_name") or request.query_params.get("config_name") or "").strip()
    if not config_name:
        raise HTTPException(
            status_code=400,
            detail="config_name is required (body.config_name or ?config_name=)",
        )
    _log_tenant_action(
        action="secrets.upsert",
        tenant_id=tenant_id,
        connector_id=cid,
        config_name=config_name,
    )
    try:
        keys = upsert_tenant_secrets(
            tenant_id,
            cid,
            {str(k): str(v) for k, v in secrets.items()},
            config_name=config_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    factory_dep.invalidate_configs(tenant_id, cid, [config_name])
    _persist_tenants(factory_dep)
    return JSONResponse(status_code=200, content={"keys": keys, "config_name": config_name})


@app.get("/v1/connectors/{cid}/secrets", tags=["config"])
async def secrets_list_keys(
    cid: str,
    request: Request,
    config_name: str = "",
) -> JSONResponse:
    from node_wire_runtime.tenant_persistence import list_secret_logical_keys

    tenant_id = _config_tenant(request)
    name = (config_name or request.query_params.get("config_name") or "").strip()
    if not name:
        raise HTTPException(
            status_code=400,
            detail="config_name query parameter is required",
        )
    return JSONResponse(
        status_code=200,
        content={"keys": list_secret_logical_keys(tenant_id, cid, name), "config_name": name},
    )


@app.post("/v1/connectors/{cid}/configs", tags=["config"])
async def config_create(
    cid: str,
    request: Request,
    doc: Dict[str, Any],
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    from node_wire_runtime.tenant_persistence import upsert_tenant_secrets

    try:
        tenant_id = _config_tenant(request)
        body = dict(doc)
        secrets = _pop_secrets(body)
        config_name = str(body.get("name") or "").strip()
        _log_tenant_action(
            action="config.create",
            tenant_id=tenant_id,
            connector_id=cid,
            config_name=config_name or None,
        )
        if secrets is not None:
            try:
                upsert_tenant_secrets(
                    tenant_id, cid, secrets, config_name=config_name
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            record = factory_dep.store.create(tenant_id, cid, body)
        except ConfigNameConflictError:
            name = str(body.get("name") or "")
            record = factory_dep.store.update(tenant_id, cid, name, body)
        _persist_tenants(factory_dep)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_config_error(exc)
    return JSONResponse(status_code=201, content={"name": record.name, "default": record.default})


@app.get("/v1/connectors/{cid}/configs", tags=["config"])
async def config_list(
    cid: str,
    request: Request,
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    # No per-list INFO: playground dropdown refresh would flood the terminal.
    tenant_id = _config_tenant(request)
    return JSONResponse(status_code=200, content=factory_dep.store.list(tenant_id, cid))


@app.get("/v1/connectors/{cid}/configs/{name}", tags=["config"])
async def config_get(
    cid: str,
    name: str,
    request: Request,
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    tenant_id = _config_tenant(request)
    doc = factory_dep.store.get(tenant_id, cid, name)
    if doc is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return JSONResponse(status_code=200, content=doc)


@app.put("/v1/connectors/{cid}/configs/{name}", tags=["config"])
async def config_update(
    cid: str,
    name: str,
    request: Request,
    doc: Dict[str, Any],
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    from node_wire_runtime.tenant_persistence import upsert_tenant_secrets

    try:
        tenant_id = _config_tenant(request)
        body = dict(doc)
        secrets = _pop_secrets(body)
        _log_tenant_action(
            action="config.update",
            tenant_id=tenant_id,
            connector_id=cid,
            config_name=name,
        )
        if secrets:
            try:
                upsert_tenant_secrets(tenant_id, cid, secrets, config_name=name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        record = factory_dep.store.update(tenant_id, cid, name, body)
        _persist_tenants(factory_dep)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_config_error(exc)
    return JSONResponse(status_code=200, content={"name": record.name, "default": record.default})


@app.delete("/v1/connectors/{cid}/configs/{name}", tags=["config"])
async def config_delete(
    cid: str,
    name: str,
    request: Request,
    new_default: str | None = None,
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    try:
        tenant_id = _config_tenant(request)
        _log_tenant_action(
            action="config.delete",
            tenant_id=tenant_id,
            connector_id=cid,
            config_name=name,
        )
        factory_dep.store.delete(tenant_id, cid, name, new_default=new_default)
        # Secrets are per named config; clear this config's vault.
        from node_wire_runtime.tenant_persistence import (
            clear_config_secrets,
            clear_tenant_connector_secrets,
        )

        clear_config_secrets(tenant_id, cid, name)
        if not factory_dep.store.has_config(tenant_id, cid):
            clear_tenant_connector_secrets(tenant_id, cid)
        _persist_tenants(factory_dep)
    except Exception as exc:  # noqa: BLE001
        raise _map_config_error(exc)
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.put("/v1/connectors/{cid}/configs/{name}/default", tags=["config"])
async def config_set_default(
    cid: str,
    name: str,
    request: Request,
    factory_dep: ConnectorFactory = Depends(get_factory),
) -> JSONResponse:
    try:
        tenant_id = _config_tenant(request)
        _log_tenant_action(
            action="config.set_default",
            tenant_id=tenant_id,
            connector_id=cid,
            config_name=name,
        )
        factory_dep.store.set_default(tenant_id, cid, name)
        _persist_tenants(factory_dep)
    except Exception as exc:  # noqa: BLE001
        raise _map_config_error(exc)
    return JSONResponse(status_code=200, content={"status": "ok"})

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


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _rate_limit_enabled() -> bool:
    return _truthy(os.environ.get("NW_REST_RATE_LIMIT_ENABLED"))


def _get_rate_limiter() -> InMemoryRateLimiter:
    global _rate_limiter, _rate_limiter_cfg
    max_requests = int(os.environ.get("NW_REST_RATE_LIMIT_MAX_REQUESTS", "120"))
    window_seconds = int(os.environ.get("NW_REST_RATE_LIMIT_WINDOW_SECONDS", "60"))
    max_tracked_keys = int(os.environ.get("NW_REST_RATE_LIMIT_MAX_TRACKED_KEYS", "10000"))
    key_ttl_seconds = int(os.environ.get("NW_REST_RATE_LIMIT_KEY_TTL_SECONDS", "3600"))
    cfg = (max_requests, window_seconds, max_tracked_keys, key_ttl_seconds)
    if _rate_limiter is None or _rate_limiter_cfg != cfg:
        _rate_limiter = InMemoryRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            max_tracked_keys=max_tracked_keys,
            key_ttl_seconds=key_ttl_seconds,
        )
        _rate_limiter_cfg = cfg
    return _rate_limiter


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

        rest_id = get_rest_caller_identity(request)
        try:
            tenant_id = resolve_tenant_id(headers=request.headers, jwt_identity=rest_id)
        except MissingTenantError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        run_payload = dict(payload)
        # config_name is a resolution-time argument, never a connector input.
        # Suppressed when multitenancy is disabled so legacy path is always used.
        config_name = resolve_config_name(run_payload.pop("config_name", None))
        _log_tenant_action(
            action=f"rest.{act}",
            tenant_id=tenant_id,
            connector_id=cid,
            config_name=config_name,
        )

        if _rate_limit_enabled():
            limiter = _get_rate_limiter()
            identity_key = get_request_identity_key(request)
            rate_key = f"{tenant_id}:{cid}:{act}:{identity_key}"
            result = limiter.consume(rate_key)
            if not result.allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(result.retry_after_seconds)},
                )

        try:
            response: ConnectorResponse = await invoke(
                factory_dep,
                connector_id=cid,
                action=act,
                payload=run_payload,
                protocol="rest",
                tenant_id=tenant_id,
                config_name=config_name,
                principal=rest_id.principal if rest_id else None,
                scopes=rest_id.scopes if rest_id else None,
            )
        except ConnectorNotExposed:
            raise HTTPException(status_code=404, detail="Connector not available for REST")
        except ConfigNotFoundError:
            # Unknown scope and unknown config name return the same body so config
            # names cannot be enumerated (fail-closed).
            raise HTTPException(
                status_code=403,
                detail=(
                    "No connector configuration for this tenant. "
                    "Use Add config on this connector page."
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

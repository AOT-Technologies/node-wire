"""
Generic action-spec primitives for SDK-backed connectors (e.g. googleapiclient).

Subclasses describe how validated Pydantic models map to vendor SDK calls:
resource navigation, method name, keyword/body mapping, constants, and optional
custom builders or post-processors.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel


def navigate_resource(client: Any, segments: Tuple[str, ...]) -> Any:
    """Traverse discovery-style APIs: client.files().permissions()..."""
    api = client
    for seg in segments:
        api = getattr(api, seg)()
    return api


def default_build_kwargs(
    *,
    kwargs_from_model: Dict[str, str],
    body_from_model: Optional[Dict[str, str]],
    body_constant: Optional[Dict[str, Any]],
    constant_kwargs: Dict[str, Any],
    computed_kwargs: Dict[str, Callable[[BaseModel], Any]],
    include_empty_body: bool,
    model: BaseModel,
) -> Dict[str, Any]:
    """Build SDK method kwargs from a validated input model."""
    kw: Dict[str, Any] = dict(constant_kwargs)

    for attr, sdk_name in kwargs_from_model.items():
        val = getattr(model, attr, None)
        if val is not None:
            kw[sdk_name] = val

    for sdk_name, fn in computed_kwargs.items():
        val = fn(model)
        if val is not None:
            kw[sdk_name] = val

    body: Dict[str, Any] = {}
    if body_constant:
        body.update(body_constant)
    if body_from_model:
        for attr, bkey in body_from_model.items():
            val = getattr(model, attr, None)
            if val is not None:
                body[bkey] = val

    if body_from_model is not None or body_constant is not None:
        if body or include_empty_body:
            kw["body"] = body

    return kw


@dataclass(frozen=True)
class SdkActionSpec:
    """
    Describes one vendor SDK call: resource().method(**kwargs).execute()

    When ``build_kwargs`` is None, kwargs are built from the mapping fields.
    When ``build_kwargs`` is set, it receives (client, model) and must return
    the full kwargs dict for the SDK method.
    """

    resource_segments: Tuple[str, ...]
    method_name: str
    kwargs_from_model: Dict[str, str] = field(default_factory=dict)
    body_from_model: Optional[Dict[str, str]] = None
    body_constant: Optional[Dict[str, Any]] = None
    constant_kwargs: Dict[str, Any] = field(default_factory=dict)
    computed_kwargs: Dict[str, Callable[[BaseModel], Any]] = field(default_factory=dict)
    # Pass body={} when the API requires a body key even if empty (e.g. files.update).
    include_empty_body: bool = False
    build_kwargs: Optional[Callable[[Any, BaseModel], Dict[str, Any]]] = None
    post_process: Optional[Callable[[Any, BaseModel], Any]] = None
    # Set these when the spec is declared in a connector's action_specs class var.
    # input_model is required; output_model falls back to cls.output_model if None.
    input_model: Optional[Any] = None
    output_model: Optional[Any] = None
    alias_tolerant: bool = False
    # Optional: mutates MCP tool args dict in place before connector.run (see mcp_normalizers).
    mcp_normalize: Optional[Callable[[Dict[str, Any]], None]] = None
    # Security metadata
    requires_auth: bool = True
    scopes: Optional[List[str]] = None
    rate_limit: Optional[Dict[str, Any]] = None
    deprecated: bool = False


def build_method_kwargs(spec: SdkActionSpec, client: Any, model: BaseModel) -> Dict[str, Any]:
    if spec.build_kwargs is not None:
        return spec.build_kwargs(client, model)
    return default_build_kwargs(
        kwargs_from_model=spec.kwargs_from_model,
        body_from_model=spec.body_from_model,
        body_constant=spec.body_constant,
        constant_kwargs=spec.constant_kwargs,
        computed_kwargs=spec.computed_kwargs,
        include_empty_body=spec.include_empty_body,
        model=model,
    )


def execute_spec_sync(client: Any, spec: SdkActionSpec, model: BaseModel) -> Any:
    """Run spec.method_name on navigated resource; return execute() result (sync)."""
    kwargs = build_method_kwargs(spec, client, model)
    resource_api = navigate_resource(client, spec.resource_segments)
    method = getattr(resource_api, spec.method_name)
    result = method(**kwargs).execute()
    if spec.post_process is not None:
        return spec.post_process(result, model)
    return result


async def execute_spec_in_thread(
    client: Any,
    spec: SdkActionSpec,
    model: BaseModel,
) -> Any:
    """Run execute_spec_sync in a worker thread (for sync googleapiclient)."""
    return await asyncio.to_thread(execute_spec_sync, client, spec, model)

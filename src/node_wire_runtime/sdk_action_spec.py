#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Generic action-spec primitives for SDK-backed connectors.

A spec describes how validated Pydantic models map to a single vendor SDK call:
resource navigation, method name, keyword/body mapping, constants, and optional
custom builders or post-processors. The default resolution/invocation shape
matches discovery-style clients (e.g. googleapiclient: client.files().create()
.execute()) but every step is overridable so flat-class SDKs without an
`.execute()` step (e.g. stripe.Charge.create(...)) or natively-async SDK
methods can describe their calls the same way — see `resolve_method` and
`invoke` on `SdkActionSpec`.
"""

from __future__ import annotations

import asyncio
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel


def navigate_resource(client: Any, segments: Tuple[str, ...]) -> Any:
    """Traverse discovery-style APIs: client.files().permissions()...

    .. deprecated::
        Unused by the execute path — ``default_resolve_method`` performs its own
        segment walk (honouring ``call_segments``). Retained only for backward
        compatibility and slated for removal in a future release. Prefer
        ``default_resolve_method`` / a ``resolve_method`` override.
    """
    warnings.warn(
        "navigate_resource is deprecated and unused by the execute path; "
        "use default_resolve_method (or a SdkActionSpec.resolve_method override) "
        "instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    api = client
    for seg in segments:
        api = getattr(api, seg)()
    return api


def default_resolve_method(spec: "SdkActionSpec", client: Any) -> Callable[..., Any]:
    """
    Default method resolution: walk ``resource_segments`` off ``client``, then
    look up ``method_name`` on the result.

    Each segment is called (``client.files()``) when ``spec.call_segments`` is
    True (the discovery-client default); set it False for flat-class SDKs
    where segments are plain attributes, e.g. ``resource_segments=("Charge",)``
    for ``stripe.Charge.create(...)``.
    """
    api = client
    for seg in spec.resource_segments:
        attr = getattr(api, seg)
        api = attr() if spec.call_segments else attr
    return getattr(api, spec.method_name)


def default_invoke(method: Callable[..., Any], kwargs: Dict[str, Any]) -> Any:
    """Default invocation: discovery-style ``method(**kwargs).execute()``.

    Override via ``SdkActionSpec.invoke`` for SDK methods that return their
    result directly (no ``.execute()`` step) or that are coroutine functions.
    """
    return method(**kwargs).execute()


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

    Resolution and invocation are overridable so non-discovery-style SDKs can
    reuse the same declarative shape:

    - ``call_segments`` (default True): set False when ``resource_segments``
      are plain attributes rather than zero-arg method calls.
    - ``resolve_method``: full override for how the bound method is found,
      when segment-walking doesn't fit (e.g. a segment needs an argument).
      Receives (spec, client); returns the callable to invoke.
    - ``invoke``: full override for how the resolved method is called and its
      result extracted, when there's no ``.execute()`` step. Receives
      (method, kwargs); returns the raw result (or a coroutine — see
      ``execute_spec_async``).
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
    call_segments: bool = True
    resolve_method: Optional[Callable[["SdkActionSpec", Any], Callable[..., Any]]] = None
    invoke: Optional[Callable[[Callable[..., Any], Dict[str, Any]], Any]] = None
    # Set these when the spec is declared in a connector's action_specs class var.
    # input_model is required; output_model falls back to cls.output_model if None.
    input_model: Optional[Any] = None
    output_model: Optional[Any] = None
    alias_tolerant: bool = False
    # Optional: mutates MCP tool args dict in place before connector.run. Implementations
    # live in each connector's own package (e.g. node_wire_<connector>/normalizers.py).
    mcp_normalize: Optional[Callable[[Dict[str, Any]], None]] = None
    # Security metadata
    requires_auth: bool = True
    scopes: Optional[List[str]] = None
    rate_limit: Optional[Dict[str, Any]] = None
    deprecated: bool = False

    def __post_init__(self) -> None:
        # call_segments and invoke are independent axes: flipping call_segments
        # to False (flat-class SDK) does not change how the method is invoked,
        # so the default_invoke `.execute()` step still runs and will usually
        # AttributeError on a flat SDK's return value. Flag the likely-wrong
        # combination at construction instead of at first call.
        if not self.call_segments and self.invoke is None:
            warnings.warn(
                "SdkActionSpec(call_segments=False) with no `invoke` override "
                "still uses default_invoke, which calls `.execute()` on the SDK "
                "method's return value. Flat-class SDKs (e.g. "
                "stripe.Charge.create(...)) usually have no `.execute()` step — "
                "pass an `invoke=` override that returns the result directly.",
                # __post_init__ (1) -> generated __init__ (2) -> caller (3), so
                # the warning lands on the SdkActionSpec(...) authoring site.
                stacklevel=3,
            )


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


def _resolve_and_invoke(client: Any, spec: SdkActionSpec, model: BaseModel) -> Any:
    """Shared pipeline: build kwargs, resolve the bound method, invoke it.

    Returns the raw invocation result *before* post-processing (and without
    awaiting a coroutine) so both the sync and async execute paths can layer
    their own coroutine handling on top without duplicating this logic.
    """
    kwargs = build_method_kwargs(spec, client, model)
    resolver = spec.resolve_method or default_resolve_method
    method = resolver(spec, client)
    invoker = spec.invoke or default_invoke
    return invoker(method, kwargs)


def execute_spec_sync(client: Any, spec: SdkActionSpec, model: BaseModel) -> Any:
    """Resolve spec's method, invoke it, and return the raw (synchronous) result.

    This does not await: an ``invoke`` that returns a coroutine is a caller
    error on the sync path (including when wrapped by ``execute_spec_in_thread``)
    and raises ``RuntimeError`` rather than silently returning an un-awaited
    coroutine. Use ``execute_spec_async`` for natively-async SDK methods.
    """
    result = _resolve_and_invoke(client, spec, model)
    if asyncio.iscoroutine(result):
        result.close()  # suppress the "coroutine was never awaited" warning
        raise RuntimeError(
            "spec.invoke returned a coroutine on a synchronous execution path; "
            "use execute_spec_async (not execute_spec_sync / "
            "execute_spec_in_thread) for natively-async SDK methods."
        )
    if spec.post_process is not None:
        return spec.post_process(result, model)
    return result


async def execute_spec_in_thread(
    client: Any,
    spec: SdkActionSpec,
    model: BaseModel,
) -> Any:
    """Run execute_spec_sync in a worker thread (for blocking SDKs, e.g. googleapiclient)."""
    return await asyncio.to_thread(execute_spec_sync, client, spec, model)


async def execute_spec_async(client: Any, spec: SdkActionSpec, model: BaseModel) -> Any:
    """
    Resolve and invoke spec's method directly on the running event loop, for
    natively-async SDK methods. Use this instead of ``execute_spec_in_thread``
    when the vendor SDK's methods are themselves coroutine functions — no
    thread offload is needed (or wanted) in that case.
    """
    result = _resolve_and_invoke(client, spec, model)
    if asyncio.iscoroutine(result):
        result = await result
    if spec.post_process is not None:
        return spec.post_process(result, model)
    return result

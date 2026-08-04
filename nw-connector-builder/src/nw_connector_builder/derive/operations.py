# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Operation → ActionPlan derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from nw_connector_builder.derive.auth import (
    ConnectorAuthPlan,
    OpSecurityDecision,
    build_auth_plan,
    choose_connector_scheme,
    connector_fingerprint,
    evaluate_operation_security,
)
from nw_connector_builder.derive.naming import (
    fallback_operation_name,
    uniquify_names,
)


HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


@dataclass
class ParamPlan:
    field_name: str
    wire_name: str
    location: str  # path|query|header|body
    required: bool
    schema: dict[str, Any]
    style: str | None = None
    explode: bool | None = None
    media_type: str | None = None
    python_type_hint: str = "Any"
    default: Any = None


@dataclass
class ActionPlan:
    name: str
    method: str
    path: str
    operation: dict[str, Any]
    params: list[ParamPlan]
    body_schema: dict[str, Any] | None
    body_media_type: str | None
    output_schema: dict[str, Any] | None
    use_rest_response_output: bool
    auth: bool  # False for anonymous
    deprecated: bool = False
    examples: dict[str, Any] = field(default_factory=dict)


@dataclass
class SoftDrop:
    method: str
    path: str
    operation_id: str | None
    reason: str


@dataclass
class DeriveResult:
    actions: list[ActionPlan]
    drops: list[SoftDrop]
    auth_plan: ConnectorAuthPlan
    default_base_url: str
    coverage_warning: bool
    total_operations: int
    notes: list[str] = field(default_factory=list)


class DeriveError(Exception):
    """Hard failure during derivation."""


def _sanitize_field_name(wire: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", wire)
    name = re.sub(r"_+", "_", name).strip("_").lower()
    if not name:
        name = "field"
    if name[0].isdigit():
        name = "f_" + name
    if name in {"body", "action", "self", "cls"}:
        name = name + "_param"
    return name


def _dedupe_field_names(params: list[ParamPlan]) -> list[ParamPlan]:
    """Ensure sanitized field names are unique within an action (document order)."""
    used: set[str] = set()
    for p in params:
        base = p.field_name
        name = base
        n = 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        p.field_name = name
    return params


def _schema_type_hint(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "Any"
    if "$ref" in schema:
        # Generator does not emit component classes; avoid dangling annotations.
        return "Any"
    t = schema.get("type")
    if t == "string":
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        items = schema.get("items") or {}
        return f"list[{_schema_type_hint(items)}]"
    if t == "object":
        return "dict[str, Any]"
    # 3.1 nullable
    if "anyOf" in schema or "oneOf" in schema:
        return "Any"
    return "Any"


def _param_serialization_supported(param: dict[str, Any]) -> str | None:
    loc = param.get("in")
    if loc == "cookie":
        return "in: cookie not supported"
    if param.get("x_nw_unsupported_collection_format"):
        return (
            f"unsupported collectionFormat "
            f"{param['x_nw_unsupported_collection_format']}"
        )
    style = param.get("style")
    if loc == "path":
        if style and style not in {"simple"}:
            return f"unsupported path style: {style}"
    elif loc == "query":
        if style and style not in {"form"}:
            return f"unsupported query style: {style}"
        if style == "deepObject":
            return "unsupported style: deepObject"
    elif loc == "header":
        if style and style not in {"simple"}:
            return f"unsupported header style: {style}"
    return None


def _pick_json_media(content: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if not content:
        return None
    if "application/json" in content:
        return "application/json", content["application/json"]
    for mt, body in content.items():
        if mt.endswith("+json") or "json" in mt:
            return mt, body
    # first
    mt = next(iter(content.keys()))
    return mt, content[mt]


def _success_response_schema(op: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(schema, use_envelope)``.

    Typed (non-envelope) output is only used for JSON **object** schemas. Array
    and scalar success bodies go through ``RestResponseOutput`` so
    ``model_validate`` is never asked to parse a list/scalar into an empty
    BaseModel (which would raise at call time).
    """
    responses = op.get("responses") or {}
    # explicit 2xx ascending
    codes = []
    for code in responses:
        if str(code).isdigit() and 200 <= int(code) < 300:
            codes.append(int(code))
    codes.sort()
    ordered_keys: list[str] = [str(c) for c in codes]
    for patterned in ("2XX", "2xx"):
        if patterned in responses:
            ordered_keys.append(patterned)

    for key in ordered_keys:
        resp = responses.get(key) or {}
        content = resp.get("content") or {}
        picked = _pick_json_media(content)
        if picked:
            _mt, media = picked
            schema = media.get("schema")
            if schema:
                if _schema_is_object(schema):
                    return schema, False
                # array / scalar / unresolved $ref → envelope
                return schema, True
        # 204 / no content
        if not content:
            return None, True

    # no success JSON → envelope
    return None, True


def _schema_is_object(schema: dict[str, Any]) -> bool:
    """True when the schema is (or clearly describes) a JSON object."""
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        # Unresolved refs are unsafe for empty typed models; prefer envelope.
        return False
    t = schema.get("type")
    if t == "object":
        return True
    if t is not None:
        return False
    # typeless but object-shaped
    return "properties" in schema or "allOf" in schema or "additionalProperties" in schema


def resolve_base_url(doc: dict[str, Any], override: str | None) -> str:
    if override:
        return override.rstrip("/")
    servers = doc.get("servers") or []
    if not servers:
        raise DeriveError("No servers in OpenAPI document; pass --base-url")
    server = servers[0]
    url = server.get("url") or ""
    variables = server.get("variables") or {}
    for name, var in variables.items():
        default = var.get("default") if isinstance(var, dict) else None
        if default is None:
            raise DeriveError(
                f"Server variable {name!r} has no default; pass --base-url or fix the spec"
            )
        url = url.replace("{" + name + "}", str(default))
    parsed = urlparse(url)
    if not parsed.scheme or url.startswith("/"):
        raise DeriveError(
            f"Resolved server URL is relative ({url!r}); pass an absolute --base-url"
        )
    return url.rstrip("/")


def derive_operations(
    doc: dict[str, Any],
    *,
    connector_id: str,
    base_url_override: str | None = None,
) -> DeriveResult:
    schemes = ((doc.get("components") or {}).get("securitySchemes")) or {}
    chosen = choose_connector_scheme(doc, schemes)
    fp = connector_fingerprint(chosen, schemes)
    auth_plan = build_auth_plan(connector_id, schemes, chosen)
    default_base_url = resolve_base_url(doc, base_url_override)

    notes: list[str] = []
    if any(
        isinstance(item, dict) and item.get("servers")
        for item in (doc.get("paths") or {}).values()
    ):
        notes.append("Operation/path-level servers present but ignored in v1")

    raw_ops: list[tuple[str, str, dict[str, Any], str]] = []
    # (method, path, op, candidate_name)
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared_params = list(item.get("parameters") or [])
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            op = dict(op)
            op_params = shared_params + list(op.get("parameters") or [])
            op["_nw_merged_params"] = op_params
            oid = op.get("operationId")
            cand = str(oid) if oid else fallback_operation_name(method, path)
            raw_ops.append((method.lower(), path, op, cand))

    total = len(raw_ops)
    names = uniquify_names([c for *_, c in raw_ops])

    actions: list[ActionPlan] = []
    drops: list[SoftDrop] = []

    for (method, path, op, _cand), name in zip(raw_ops, names):
        sec = evaluate_operation_security(
            op.get("security"), doc.get("security"), schemes, fp
        )
        if sec.mode in {"unsupported", "divergent", "and_multi"}:
            drops.append(
                SoftDrop(method, path, op.get("operationId"), sec.reason or sec.mode)
            )
            continue

        # params
        param_plans: list[ParamPlan] = []
        drop_reason = None
        for param in op.get("_nw_merged_params") or []:
            if not isinstance(param, dict):
                continue
            if "$ref" in param:
                drop_reason = f"unresolved parameter $ref: {param['$ref']}"
                break
            bad = _param_serialization_supported(param)
            if bad:
                drop_reason = bad
                break
            loc = param.get("in")
            wire = param.get("name") or "param"
            schema = param.get("schema") or {}
            if param.get("type") and not schema:
                schema = {k: param[k] for k in ("type", "format", "items", "enum", "default") if k in param}
            style = param.get("style")
            explode = param.get("explode")
            required = bool(param.get("required")) or loc == "path"
            param_plans.append(
                ParamPlan(
                    field_name=_sanitize_field_name(wire),
                    wire_name=wire,
                    location=loc,
                    required=required,
                    schema=schema,
                    style=style,
                    explode=explode,
                    python_type_hint=_schema_type_hint(schema),
                    default=schema.get("default", param.get("default")),
                )
            )

        if drop_reason:
            drops.append(SoftDrop(method, path, op.get("operationId"), drop_reason))
            continue

        param_plans = _dedupe_field_names(param_plans)

        body_schema = None
        body_media = None
        rb = op.get("requestBody")
        if isinstance(rb, dict):
            content = rb.get("content") or {}
            picked = _pick_json_media(content)
            if picked:
                body_media, media = picked
                body_schema = media.get("schema")
            elif content:
                # non-JSON request body — still implement
                body_media = next(iter(content.keys()))
                body_schema = (content[body_media] or {}).get("schema")

        out_schema, use_envelope = _success_response_schema(op)

        examples: dict[str, Any] = {}
        # Collect request/response examples when present
        if isinstance(rb, dict):
            content = rb.get("content") or {}
            for mt, media in content.items():
                if isinstance(media, dict) and "example" in media:
                    examples.setdefault("request", media["example"])
                if isinstance(media, dict) and "examples" in media:
                    ex = media["examples"]
                    if isinstance(ex, dict) and ex:
                        first = next(iter(ex.values()))
                        if isinstance(first, dict) and "value" in first:
                            examples.setdefault("request", first["value"])

        actions.append(
            ActionPlan(
                name=name,
                method=method.upper(),
                path=path,
                operation=op,
                params=param_plans,
                body_schema=body_schema,
                body_media_type=body_media,
                output_schema=out_schema,
                use_rest_response_output=use_envelope,
                auth=sec.mode != "anonymous",
                deprecated=bool(op.get("deprecated")),
                examples=examples,
            )
        )

    if not actions:
        raise DeriveError("Zero usable operations after soft-drops; cannot build connector")

    coverage_warning = len(actions) < (total * 0.5)
    return DeriveResult(
        actions=actions,
        drops=drops,
        auth_plan=auth_plan,
        default_base_url=default_base_url,
        coverage_warning=coverage_warning,
        total_operations=total,
        notes=notes,
    )

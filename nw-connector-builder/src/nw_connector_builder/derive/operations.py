# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Operation → ActionPlan derivation."""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from nw_connector_builder.derive.auth import (
    ConnectorAuthPlan,
    build_auth_plan,
    choose_connector_scheme,
    connector_fingerprint,
    evaluate_operation_security,
    uniquify_extra_secret_keys,
)
from nw_connector_builder.derive.naming import (
    action_name_budget,
    fallback_operation_name,
    uniquify_names,
)


# OpenAPI 3.2 adds `query` (the HTTP QUERY method); other methods arrive through a path
# item's `additionalOperations` map.
HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "query"}
)


@dataclass
class ParamPlan:
    field_name: str
    wire_name: str
    # path|query|header, or body_property: one property of an object request body,
    # reassembled into the body by the runtime (see split_params_by_location).
    location: str
    required: bool
    schema: dict[str, Any]
    style: str | None = None
    explode: bool | None = None
    media_type: str | None = None
    python_type_hint: str = "Any"
    default: Any = None
    description: str | None = None
    # `allowReserved: true` on a query parameter: reserved characters are sent as-is.
    allow_reserved: bool = False
    # Multipart only: the Encoding Object's contentType for this property's part.
    part_content_type: str | None = None


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
    # Success-flag field the API sets false to report failure inside a 2xx body.
    envelope_ok_field: str | None = None
    deprecated: bool = False
    examples: dict[str, Any] = field(default_factory=dict)
    # Non-default scheme for divergent ops; None = connector default. A tuple for an
    # AND requirement: every scheme to send, None standing for the connector default.
    auth_scheme_name: str | tuple[str | None, ...] | None = None
    # An operation- or path-level `servers` entry that differs from the connector's base
    # URL (GitHub sends release-asset uploads to uploads.github.com).
    server_url: str | None = None
    # True when the body's properties were emitted as body_property params; the
    # action then has no single `body` argument.
    body_flattened: bool = False
    # requestBody.required — for a whole body, whether `body` is a required argument.
    body_required: bool = False


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
    # Non-default schemes used by >=1 generated action; empty for single-scheme connectors.
    extra_auth_plans: dict[str, ConnectorAuthPlan] = field(default_factory=dict)
    # `--header NAME=VALUE`: sent on every request (connectors.yaml `headers:`); set by the
    # pipeline, not derived from the spec.
    static_headers: dict[str, str] = field(default_factory=dict)


class DeriveError(Exception):
    """Hard failure during derivation."""


# Argument names the bindings or the generated model claim for themselves: MCP
# pops tenant_id/config_name from tool arguments before the connector sees them,
# `action` is the dispatch discriminator, `body` the whole-body field.
_RESERVED_FIELD_NAMES = frozenset(
    {"body", "action", "self", "cls", "config_name", "tenant_id", "trace_id"}
)


def _sanitize_field_name(wire: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", wire)
    name = re.sub(r"_+", "_", name).strip("_").lower()
    if not name:
        name = "field"
    if name[0].isdigit():
        name = "f_" + name
    if name in _RESERVED_FIELD_NAMES:
        name = name + "_param"
    elif keyword.iskeyword(name) or hasattr(BaseModel, name):
        # `from`, `class`, … are not valid identifiers; `json`, `copy`, … would
        # shadow BaseModel attributes. The wire name is unaffected.
        name = name + "_"
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


# Parameter names that carry the API credential itself. For an authenticated
# action the configured AuthProvider already attaches the credential, so keeping
# these as call arguments duplicates it — and on an MCP tool it makes the model
# refuse to act until a human hands it a secret to paste into a tool call.
# Matched exactly, so ordinary fields like `page_token` or `cursor` are untouched.
_CREDENTIAL_PARAM_NAMES = frozenset(
    {
        "token",
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "api-key",
        "auth_token",
        "authorization",
        "password",
        "secret",
    }
)


def _is_credential_param(wire_name: str, location: str) -> bool:
    """True for a header/query/body field that duplicates the connector's credential.

    Path parameters are never dropped: they are part of the URL.
    """
    if location not in {"header", "query", "body_property"}:
        return False
    return wire_name.strip().lower() in _CREDENTIAL_PARAM_NAMES


# Media types whose body is a set of named fields the runtime can rebuild from
# body_property arguments (see node_wire_runtime.rest._encode_request_body).
def _is_field_media(media_type: str | None) -> bool:
    mt = (media_type or "").lower()
    return (
        mt == "application/json"
        or mt.endswith("+json")
        or mt == "application/x-www-form-urlencoded"
        or mt.startswith("multipart/")
    )


def _object_properties(schema: Any) -> tuple[dict[str, Any], list[str]] | None:
    """``(properties, required)`` of an object schema with declared properties, else None.

    Top-level ``allOf`` parts are merged. Anything that cannot be described as one
    flat set of named fields — ``oneOf``/``anyOf``, unresolved ``$ref``, a
    non-object type, free-form maps — returns None and keeps a whole-body argument.
    """
    if not isinstance(schema, dict) or "$ref" in schema:
        return None
    if schema.get("oneOf") or schema.get("anyOf"):
        return None
    parts = [schema, *(schema.get("allOf") or [])]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or "$ref" in part:
            return None
        if part.get("type") not in (None, "object"):
            return None
        props = part.get("properties") or {}
        if not isinstance(props, dict):
            return None
        properties.update(props)
        required.extend(str(r) for r in part.get("required") or [])
    if not properties:
        return None
    return properties, list(dict.fromkeys(required))


def _body_property_plans(
    schema: Any,
    *,
    media_type: str,
    body_required: bool,
    encoding: dict[str, Any] | None = None,
) -> list[ParamPlan] | None:
    """One ``body_property`` ParamPlan per writable property, or None to keep ``body`` whole.

    A property is required only when the body itself is required: an optional
    body may be omitted entirely, required fields and all.
    """
    if not _is_field_media(media_type):
        return None
    shape = _object_properties(schema)
    if shape is None:
        return None
    properties, required = shape
    plans: list[ParamPlan] = []
    for wire, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        if prop.get("readOnly"):
            # readOnly: sent by the server in responses, never by a client.
            continue
        plans.append(
            ParamPlan(
                field_name=_sanitize_field_name(str(wire)),
                wire_name=str(wire),
                location="body_property",
                required=body_required and str(wire) in required,
                schema=prop,
                media_type=media_type,
                python_type_hint=_schema_type_hint(prop),
                default=prop.get("default"),
                description=prop.get("description"),
                part_content_type=(
                    str((encoding or {}).get(wire, {}).get("contentType") or "") or None
                    if media_type.lower().startswith("multipart/")
                    and isinstance((encoding or {}).get(wire), dict)
                    else None
                ),
            )
        )
    return plans


def _rename_colliding_params(params: list[ParamPlan], body_props: list[ParamPlan]) -> None:
    """Rename path/query/header params whose name a body property also uses.

    The body property keeps the plain name — it is usually the one callers mean —
    and the parameter becomes ``<name>__<location>``. The wire name is unchanged.
    """
    taken = {p.field_name for p in body_props}
    for p in params:
        if p.field_name in taken:
            p.field_name = f"{p.field_name}__{p.location}"


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


# Every style OpenAPI defines for each location; node_wire_runtime.rest sends them all.
_SUPPORTED_STYLES = {
    "path": {"simple", "label", "matrix"},
    "query": {"form", "spaceDelimited", "pipeDelimited", "deepObject"},
    "header": {"simple"},
    "cookie": {"form", "cookie"},
    "querystring": set(),
}
# OAS: "If in is header and the name is Accept, Content-Type or Authorization, the
# parameter definition SHALL be ignored." The body media type and auth set them.
_IGNORED_HEADER_PARAMS = frozenset({"accept", "content-type", "authorization"})


def _param_serialization_supported(param: dict[str, Any]) -> str | None:
    loc = param.get("in")
    if loc not in _SUPPORTED_STYLES:
        return f"unknown parameter location: in: {loc}"
    if param.get("x-nw-unsupported-collection-format"):
        return f"unsupported collectionFormat {param['x-nw-unsupported-collection-format']}"
    style = param.get("style")
    if style and style not in _SUPPORTED_STYLES[str(loc)]:
        return f"unsupported {loc} style: {style}"
    return None


def _merge_parameters(shared: list[Any], own: list[Any]) -> list[Any]:
    """Path-level parameters plus the operation's; the operation's win by ``(name, in)``.

    OAS: an operation parameter "can override" a path-level one with the same name and
    location, but "cannot remove" it. Concatenating would emit two arguments for one
    wire parameter.
    """
    merged: dict[Any, Any] = {}
    for param in [*shared, *own]:
        key = (
            (param.get("name"), param.get("in"))
            if isinstance(param, dict) and "$ref" not in param
            else id(param)
        )
        merged[key] = param
    return list(merged.values())


def _param_schema(param: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """``(schema, media type)``: from ``schema``, or from ``content`` (serialized as that media)."""
    content = param.get("content")
    if isinstance(content, dict) and content:
        media, media_obj = next(iter(content.items()))
        schema = media_obj.get("schema") if isinstance(media_obj, dict) else None
        return (schema if isinstance(schema, dict) else {}), str(media)
    schema = param.get("schema")
    # 3.1 allows a boolean schema (`true`: anything; `false`: nothing); treat as free-form.
    schema = schema if isinstance(schema, dict) else {}
    if param.get("type") and not schema:
        schema = {k: param[k] for k in ("type", "format", "items", "enum", "default") if k in param}
    return schema, None


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
    if not ordered_keys and "default" in responses:
        # No success response declared: `default` is the only description of one.
        ordered_keys.append("default")

    for key in ordered_keys:
        resp = responses.get(key) or {}
        content = resp.get("content") or {}
        picked = _pick_json_media(content)
        if picked:
            mt, media = picked
            schema = media.get("schema")
            if schema:
                # A typed output parses the body as JSON: only for a JSON media type. An XML
                # or text response with an object schema is returned in the envelope.
                if _schema_is_object(schema) and "json" in mt.lower():
                    return schema, False
                # array / scalar / unresolved $ref → envelope
                return schema, True
        # 204 / no content
        if not content:
            return None, True

    # no success JSON → envelope
    return None, True


# Envelope-flag convention: a 2xx body carrying its own success boolean
# (Slack, Telegram, and other "always 200" APIs). Only this exact shape counts —
# a required boolean named `ok` — so a spec that happens to have an optional or
# non-boolean `ok` field is untouched.
_ENVELOPE_OK_FIELD = "ok"


def _envelope_ok_field(schema: dict[str, Any] | None) -> str | None:
    """Return the success-flag field name when the success schema declares one.

    Requires the flag to be **required**: an optional one says nothing when the
    API omits it, and treating its absence as failure would break every response
    that legitimately leaves it out.
    """
    if not isinstance(schema, dict):
        return None
    if _ENVELOPE_OK_FIELD not in (schema.get("required") or []):
        return None
    prop = (schema.get("properties") or {}).get(_ENVELOPE_OK_FIELD)
    if not isinstance(prop, dict) or prop.get("type") != "boolean":
        return None
    return _ENVELOPE_OK_FIELD


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
        raise DeriveError(f"Resolved server URL is relative ({url!r}); pass an absolute --base-url")
    return url.rstrip("/")


def _media_examples(media: dict[str, Any]) -> list[Any]:
    """A media type's ``example`` and its first ``examples`` value, in that order.

    OAS 3.2 Example Objects may give ``dataValue`` (the data before serialization)
    instead of ``value``.
    """
    found: list[Any] = []
    if "example" in media:
        found.append(media["example"])
    ex = media.get("examples")
    if isinstance(ex, dict) and ex:
        first = next(iter(ex.values()))
        if isinstance(first, dict):
            for key in ("value", "dataValue"):
                if key in first:
                    found.append(first[key])
                    break
    return found


def _example_matches(example: Any, schema: Any, *, oas31: bool = False) -> bool:
    """Whether ``example`` validates against ``schema`` (no schema: yes).

    ``oas31``: the document is OpenAPI 3.1 or later, whose schemas are JSON Schema 2020-12.
    """
    if not isinstance(schema, dict):
        return True
    from openapi_schema_validator import OAS30Validator, OAS31Validator

    validator = OAS31Validator if oas31 else OAS30Validator
    try:
        return validator(schema).is_valid(example)
    except Exception:  # noqa: BLE001 - an unusable schema says nothing about the example
        return True


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
    credential_params: set[str] = set()
    raw_ops: list[tuple[str, str, dict[str, Any], str]] = []
    # (method, path, op, candidate_name)
    op_servers: list[str] = []
    unusable_servers: list[str] = []
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared_params = list(item.get("parameters") or [])
        operations = [(method, op) for method, op in item.items() if method.lower() in HTTP_METHODS]
        extra_ops = item.get("additionalOperations")
        if isinstance(extra_ops, dict):
            operations.extend(extra_ops.items())
        for method, op in operations:
            if not isinstance(op, dict):
                continue
            op = dict(op)
            op["_nw_merged_params"] = _merge_parameters(
                shared_params, list(op.get("parameters") or [])
            )
            servers = op.get("servers") or item.get("servers")
            if servers:
                try:
                    server = resolve_base_url({"servers": servers}, None)
                except DeriveError:
                    unusable_servers.append(f"{method.upper()} {path}")
                else:
                    if server != default_base_url:
                        op["_nw_server_url"] = server
                        op_servers.append(f"{method.upper()} {path} → {server}")
            oid = op.get("operationId")
            cand = str(oid) if oid else fallback_operation_name(method.lower(), path)
            raw_ops.append((method.lower(), path, op, cand))
    inbound = [
        f"{len(doc['webhooks'])} webhook(s)" if isinstance(doc.get("webhooks"), dict) else "",
        f"callbacks on {n_cb} operation(s)"
        if (n_cb := sum(1 for _m, _p, o, _c in raw_ops if o.get("callbacks")))
        else "",
    ]
    if any(inbound):
        notes.append(
            "Not generated, because they are requests the API sends to you, not calls you "
            "make: " + ", ".join(i for i in inbound if i)
        )
    if op_servers:
        notes.append(
            "Operation(s) with their own server, called there instead of the base URL: "
            + ", ".join(op_servers[:10])
            + (f", +{len(op_servers) - 10} more" if len(op_servers) > 10 else "")
        )
    if unusable_servers:
        notes.append(
            "Relative or variable-without-default operation servers ignored (the base URL is "
            "used): " + ", ".join(unusable_servers)
        )

    # Removed by the loader because they fail OpenAPI validation on their own.
    invalid = [i for i in doc.get("x-nw-invalid-operations") or [] if isinstance(i, dict)]
    total = len(raw_ops) + len(invalid)
    names = uniquify_names([c for *_, c in raw_ops], max_len=action_name_budget(connector_id))

    actions: list[ActionPlan] = []
    drops: list[SoftDrop] = [
        SoftDrop(
            str(i.get("method")),
            str(i.get("path")),
            i.get("operationId"),
            f"invalid OpenAPI: {i.get('reason')}",
        )
        for i in invalid
    ]
    invalid_components = doc.get("x-nw-invalid-components") or []
    if invalid_components:
        notes.append(
            "Removed component(s) that fail OpenAPI validation: "
            + ", ".join(str(c) for c in invalid_components)
        )
    mismatched_examples: list[str] = []
    skipped_params: list[str] = []
    ignored_headers: list[str] = []
    oas31 = not str(doc.get("openapi", "3.0")).startswith("3.0")

    for (method, path, op, _cand), name in zip(raw_ops, names):
        sec = evaluate_operation_security(op.get("security"), doc.get("security"), schemes, fp)
        # "divergent" = presentable non-default scheme; route by auth_scheme_name.
        if sec.mode in {"unsupported", "and_multi"}:
            drops.append(SoftDrop(method, path, op.get("operationId"), sec.reason or sec.mode))
            continue

        # params
        param_plans: list[ParamPlan] = []
        drop_reason = None
        for param in op.get("_nw_merged_params") or []:
            if not isinstance(param, dict):
                continue
            loc = param.get("in")
            wire = param.get("name") or "param"
            required = bool(param.get("required")) or loc == "path"
            bad = (
                f"unresolved parameter $ref: {param['$ref']}"
                if "$ref" in param
                else _param_serialization_supported(param)
            )
            if bad:
                if required:
                    drop_reason = bad
                    break
                # An optional parameter the connector cannot send costs only itself.
                skipped_params.append(f"{name}.{wire} ({bad})")
                continue
            if loc == "header" and str(wire).lower() in _IGNORED_HEADER_PARAMS:
                ignored_headers.append(f"{name}.{wire}")
                continue
            schema, param_media = _param_schema(param)
            style = param.get("style")
            explode = param.get("explode")
            if sec.mode != "anonymous" and _is_credential_param(wire, str(loc)):
                credential_params.add(f"{wire} (in {loc})")
                continue
            param_plans.append(
                ParamPlan(
                    field_name=_sanitize_field_name(wire),
                    wire_name=wire,
                    location=loc,
                    required=required,
                    schema=schema,
                    style=style,
                    explode=explode,
                    media_type=param_media,
                    python_type_hint=_schema_type_hint(schema),
                    default=schema.get("default", param.get("default")),
                    # OAS 3 allows the text on the parameter or on its schema.
                    description=param.get("description") or schema.get("description"),
                    allow_reserved=bool(param.get("allowReserved")) and loc in ("query", "path"),
                )
            )

        if drop_reason:
            drops.append(SoftDrop(method, path, op.get("operationId"), drop_reason))
            continue

        body_schema = None
        body_media = None
        media: Any = None
        body_required = False
        body_flattened = False
        rb = op.get("requestBody")
        if not oas31 and method in ("get", "head", "delete"):
            # OAS 3.0: request bodies are "only supported in HTTP methods where RFC 7231 has
            # explicitly defined semantics" — not GET, HEAD or DELETE. 3.1+ allows them.
            rb = None
        if isinstance(rb, dict):
            body_required = bool(rb.get("required"))
            content = rb.get("content") or {}
            picked = _pick_json_media(content)
            if picked:
                body_media, media = picked
                body_schema = (media or {}).get("schema")
                if body_schema is None:
                    # A media type without a schema still takes a body: anything goes.
                    body_schema = {}

        if body_schema is not None and body_media is not None:
            body_props = _body_property_plans(
                body_schema,
                media_type=body_media,
                body_required=body_required,
                encoding=(media or {}).get("encoding") if isinstance(media, dict) else None,
            )
            if body_props is not None:
                body_flattened = True
                if sec.mode != "anonymous":
                    for bp in body_props:
                        if _is_credential_param(bp.wire_name, bp.location):
                            credential_params.add(f"{bp.wire_name} (in body)")
                    body_props = [
                        bp
                        for bp in body_props
                        if not _is_credential_param(bp.wire_name, bp.location)
                    ]
                _rename_colliding_params(param_plans, body_props)
                param_plans.extend(body_props)

        param_plans = _dedupe_field_names(param_plans)

        out_schema, use_envelope = _success_response_schema(op)

        examples: dict[str, Any] = {}
        # The request example becomes a generated test, so only one that matches its own
        # schema is kept: a spec whose example contradicts it (GitHub's markdown/render-raw
        # shows a JSON object for a text/plain body) would otherwise fail the build gate.
        if isinstance(rb, dict):
            content = rb.get("content") or {}
            mismatched = False
            for _mt, media in content.items():
                if not isinstance(media, dict):
                    continue
                for candidate in _media_examples(media):
                    if _example_matches(candidate, media.get("schema"), oas31=oas31):
                        examples.setdefault("request", candidate)
                    else:
                        mismatched = True
            if mismatched and "request" not in examples:
                mismatched_examples.append(name)

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
                envelope_ok_field=_envelope_ok_field(out_schema),
                deprecated=bool(op.get("deprecated")),
                examples=examples,
                auth_scheme_name=(
                    sec.scheme_name
                    if sec.mode == "divergent"
                    else (sec.scheme_names if sec.mode == "combined" else None)
                ),
                body_flattened=body_flattened,
                body_required=body_required,
                server_url=op.get("_nw_server_url"),
            )
        )

    if not actions:
        raise DeriveError("Zero usable operations after soft-drops; cannot build connector")

    if skipped_params:
        notes.append(
            "Optional parameter(s) left out because they cannot be sent: "
            + ", ".join(skipped_params)
        )
    if ignored_headers:
        notes.append(
            "Header parameter(s) ignored as OpenAPI requires (Accept, Content-Type and "
            "Authorization come from the body media type and auth): " + ", ".join(ignored_headers)
        )
    if mismatched_examples:
        notes.append(
            "Request example(s) that do not match their own schema were not used as tests: "
            + ", ".join(sorted(mismatched_examples))
        )

    coverage_warning = len(actions) < (total * 0.5)
    if credential_params:
        notes.append(
            "Dropped spec parameter(s) that duplicate the connector credential: "
            f"{', '.join(sorted(credential_params))}. The configured AuthProvider supplies the "
            "credential; exposing it as a call argument would ask the caller for a secret"
        )

    enveloped = [a.name for a in actions if a.envelope_ok_field]
    if enveloped:
        # Slack's spec puts this on 170 of 174 operations; naming them all buries
        # the rest of the report. Per-action detail stays in generated_actions.
        shown = ", ".join(enveloped[:5])
        more = f", +{len(enveloped) - 5} more" if len(enveloped) > 5 else ""
        notes.append(
            f"Success-flag envelope: {len(enveloped)} of {len(actions)} action(s) treat a 2xx "
            f"body with {_ENVELOPE_OK_FIELD}=false as a business error, because the success "
            f"schema declares {_ENVELOPE_OK_FIELD} as a required boolean ({shown}{more})"
        )
    # Plans for every non-default scheme a generated action actually uses.
    used: set[str] = set()
    for a in actions:
        if isinstance(a.auth_scheme_name, tuple):
            used.update(n for n in a.auth_scheme_name if n is not None)
        elif a.auth_scheme_name is not None:
            used.add(a.auth_scheme_name)
    extra_scheme_names = sorted(used)
    extra_auth_plans = uniquify_extra_secret_keys(
        connector_id,
        auth_plan,
        {name: build_auth_plan(connector_id, schemes, name) for name in extra_scheme_names},
    )
    return DeriveResult(
        actions=actions,
        drops=drops,
        auth_plan=auth_plan,
        default_base_url=default_base_url,
        coverage_warning=coverage_warning,
        total_operations=total,
        notes=notes,
        extra_auth_plans=extra_auth_plans,
    )

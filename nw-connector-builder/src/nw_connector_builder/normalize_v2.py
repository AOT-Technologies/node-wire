# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""In-house Swagger 2.0 → OpenAPI 3.0 normalizer (consumed subset)."""

from __future__ import annotations

import copy
from typing import Any


# Swagger 2.0 `collectionFormat` → OpenAPI 3 (style, explode), per parameter location.
# `tsv` has no OpenAPI 3 equivalent; `multi` exists only for query and formData.
_COLLECTION_FORMAT_MAP: dict[str, dict[str, tuple[str, bool]]] = {
    "csv": {
        "query": ("form", False),
        "formData": ("form", False),
        "path": ("simple", False),
        "header": ("simple", False),
    },
    "multi": {"query": ("form", True), "formData": ("form", True)},
    "ssv": {"query": ("spaceDelimited", False)},
    "pipes": {"query": ("pipeDelimited", False)},
}
_HTTP_METHODS_V2 = frozenset({"get", "put", "post", "delete", "options", "head", "patch"})


def _rewrite_refs(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/definitions/"):
                out[k] = "#/components/schemas/" + v[len("#/definitions/") :]
            else:
                out[k] = _rewrite_refs(v)
        return out
    if isinstance(obj, list):
        return [_rewrite_refs(x) for x in obj]
    return obj


def _strip_nones(obj: Any) -> Any:
    """Drop keys whose value is ``None`` (Swagger often emits null optionals)."""
    if isinstance(obj, dict):
        return {k: _strip_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nones(x) for x in obj]
    return obj


def _map_security_scheme(name: str, scheme: dict[str, Any]) -> dict[str, Any]:
    stype = scheme.get("type")
    if stype == "apiKey":
        return _strip_nones(
            {
                "type": "apiKey",
                "name": scheme.get("name"),
                "in": scheme.get("in"),
                "description": scheme.get("description"),
            }
        )
    if stype == "basic":
        return _strip_nones(
            {"type": "http", "scheme": "basic", "description": scheme.get("description")}
        )
    if stype == "oauth2":
        flows: dict[str, Any] = {}
        flow = scheme.get("flow")
        scopes = scheme.get("scopes") or {}
        if flow == "implicit":
            flows["implicit"] = {
                "authorizationUrl": scheme.get("authorizationUrl", ""),
                "scopes": scopes,
            }
        elif flow == "password":
            flows["password"] = {"tokenUrl": scheme.get("tokenUrl", ""), "scopes": scopes}
        elif flow == "application":
            flows["clientCredentials"] = {
                "tokenUrl": scheme.get("tokenUrl", ""),
                "scopes": scopes,
            }
        elif flow == "accessCode":
            flows["authorizationCode"] = {
                "authorizationUrl": scheme.get("authorizationUrl", ""),
                "tokenUrl": scheme.get("tokenUrl", ""),
                "scopes": scopes,
            }
        return _strip_nones(
            {"type": "oauth2", "flows": flows, "description": scheme.get("description")}
        )
    # pass through unknown
    return _strip_nones(dict(scheme))


_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "items",
        "enum",
        "default",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "multipleOf",
    }
)


def _param_collection_to_style(param: dict[str, Any]) -> None:
    cf = param.pop("collectionFormat", None)
    if cf is None:
        return
    mapped = _COLLECTION_FORMAT_MAP.get(cf, {}).get(str(param.get("in")))
    if mapped:
        param["style"], param["explode"] = mapped
    else:
        # Mark unsupported for later soft-drop
        param["x-nw-unsupported-collection-format"] = f"{cf} (in {param.get('in')})"


def _prefer_json(media_types: list[str]) -> str | None:
    """The media type to use from a ``consumes`` / ``produces`` list: JSON first."""
    for mt in media_types:
        if mt.lower() == "application/json":
            return mt
    for mt in media_types:
        if "json" in mt.lower():
            return mt
    return media_types[0] if media_types else None


def _inline_global_refs(src: dict[str, Any]) -> dict[str, Any]:
    """Replace ``#/parameters/X`` and ``#/responses/X`` refs with copies of their targets.

    Swagger 2.0 keeps reusable parameters and responses at the root. OpenAPI 3 has no
    home for a reusable ``body`` / ``formData`` parameter, so they are inlined before
    conversion instead of being moved to ``components``.
    """
    roots = {
        "#/parameters/": src.get("parameters") or {},
        "#/responses/": src.get("responses") or {},
    }

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                for prefix, table in roots.items():
                    if ref.startswith(prefix) and ref[len(prefix) :] in table:
                        return _walk(copy.deepcopy(table[ref[len(prefix) :]]))
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(src.get("paths") or {})


def _convert_parameters(
    params: list[dict[str, Any]],
    *,
    consumes: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Split body/formData into requestBody; return remaining params + optional requestBody."""
    kept: list[dict[str, Any]] = []
    body_schema: dict[str, Any] | None = None
    body_required = False
    form_props: dict[str, Any] = {}
    form_required: list[str] = []
    form_is_multipart = False

    for raw in params:
        p = copy.deepcopy(raw)
        if "$ref" in p:
            kept.append(_rewrite_refs(p))
            continue
        loc = p.get("in")
        _param_collection_to_style(p)

        if loc == "body":
            body_schema = _rewrite_refs(p.get("schema") or {"type": "object"})
            body_required = bool(p.get("required"))
            continue

        if loc == "formData":
            name = p.get("name", "field")
            schema: dict[str, Any] = {}
            if p.get("type") == "file":
                schema = {"type": "string", "format": "binary"}
                form_is_multipart = True
            else:
                for key in list(p.keys()):
                    if key in _SCHEMA_KEYS or key == "description":
                        if key in p:
                            schema[key] = p[key]
            form_props[name] = _rewrite_refs(schema)
            if p.get("required"):
                form_required.append(name)
            continue

        # path / query / header — move schema keywords into ``schema``
        schema = dict(p.pop("schema", None) or {})
        for key in list(p.keys()):
            if key in _SCHEMA_KEYS:
                schema[key] = p.pop(key)
        if schema:
            p["schema"] = _rewrite_refs(schema)
        kept.append(_rewrite_refs(p))

    request_body = None
    if body_schema is not None:
        media = _prefer_json(consumes) or "application/json"
        # OAS 2 `in: body` is optional unless it says `required: true`.
        request_body = {"content": {media: {"schema": body_schema}}, "required": body_required}
    elif form_props:
        multipart = form_is_multipart or any(
            mt.lower().startswith("multipart/form-data") for mt in consumes
        )
        media = "multipart/form-data" if multipart else "application/x-www-form-urlencoded"
        schema = {"type": "object", "properties": form_props}
        if form_required:
            schema["required"] = form_required
        request_body = {"content": {media: {"schema": schema}}, "required": bool(form_required)}

    return kept, request_body


def _convert_header(header: dict[str, Any]) -> dict[str, Any]:
    """Swagger 2 response header → OpenAPI 3 header (schema wrapper)."""
    if "$ref" in header:
        return _rewrite_refs(header)
    h = copy.deepcopy(header)
    # Headers only ever use style `simple` (comma-separated) in OpenAPI 3.
    h.pop("collectionFormat", None)
    schema = dict(h.pop("schema", None) or {})
    for key in list(h.keys()):
        if key in _SCHEMA_KEYS:
            schema[key] = h.pop(key)
    if schema:
        h["schema"] = _rewrite_refs(schema)
    return _rewrite_refs(h)


def _convert_responses(
    responses: dict[str, Any],
    *,
    produces: list[str],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code, resp in responses.items():
        r = copy.deepcopy(resp)
        if "$ref" in r:
            out[code] = _rewrite_refs(r)
            continue
        schema = r.pop("schema", None)
        if schema is not None:
            media = _prefer_json(produces) or "application/json"
            r["content"] = {media: {"schema": _rewrite_refs(schema)}}
        # Swagger 2.0 puts response examples on the response object, keyed by mime
        # type; OpenAPI 3 has no such property (it lives under content.<mt>.example)
        # and validation rejects the leftover key outright — Slack's published spec
        # carries one on every operation's `default` response.
        examples = r.pop("examples", None)
        if isinstance(examples, dict):
            content = r.setdefault("content", {})
            for mime, value in examples.items():
                if isinstance(content.get(mime), dict):
                    content[mime]["example"] = value
                else:
                    content[str(mime)] = {"example": value}
        headers = r.get("headers")
        if isinstance(headers, dict):
            r["headers"] = {name: _convert_header(h) for name, h in headers.items()}
        out[code] = _rewrite_refs(r)
    return out


def _sanitize_schema_node(node: dict[str, Any], counts: dict[str, int]) -> None:
    """Rewrite one node's draft-4-only constructs into their OAS 3.0 equivalents.

    JSON Schema draft-4 allows a union ``type`` list and a tuple-form ``items``
    array; OpenAPI 3.0's Schema Object allows neither, so a document carrying them
    fails validation outright even though every other part of it is usable. Real
    Swagger 2.0 documents carry them anyway — Slack's published Web API spec has
    62 between the two — so repair them rather than reject the whole spec.
    """
    types = node.get("type")
    if isinstance(types, list) and not all(isinstance(t, str) for t in types):
        types = None
    if types == "null":
        # Draft-4 spells "this is always null" as a type; OAS 3.0 has no null type.
        node.pop("type", None)
        node["nullable"] = True
        counts["null_types"] = counts.get("null_types", 0) + 1
    elif isinstance(types, list):
        concrete = [t for t in types if t != "null"]
        if len(concrete) < len(types):
            node["nullable"] = True
        if len(concrete) == 1:
            node["type"] = concrete[0]
        elif not concrete:
            node.pop("type", None)
        else:
            # OAS 3.0 has no union type; anyOf is the documented equivalent.
            node.pop("type", None)
            node["anyOf"] = [{"type": t} for t in concrete]
        counts["type_unions"] = counts.get("type_unions", 0) + 1

    items = node.get("items")
    if isinstance(items, list) and all(isinstance(i, dict) for i in items):
        concrete_items = [i for i in items if not (isinstance(i, dict) and i.get("type") == "null")]
        nullable = len(concrete_items) < len(items)
        if len(concrete_items) == 1:
            merged = dict(concrete_items[0])
        elif not concrete_items:
            merged = {}
        else:
            # Tuple form means "position 0 is A, position 1 is B", which no
            # generated model can express; the specs that use it mean "A or B".
            merged = {"anyOf": concrete_items}
        if nullable:
            merged["nullable"] = True
        node["items"] = merged
        counts["item_tuples"] = counts.get("item_tuples", 0) + 1


# Keys whose values are instance data (examples, defaults, enum members), not schemas.
# A payload such as ``{"items": ["a", "b"]}`` must not be "repaired" as a tuple schema.
_DATA_KEYS = frozenset({"example", "examples", "default", "enum", "const"})
# Maps keyed by user-chosen names: a property may be called ``example`` or ``default``,
# and ``responses`` has a real ``default`` entry. Their keys are never data keys.
_NAME_MAP_KEYS = frozenset(
    {
        "properties",
        "patternProperties",
        "definitions",
        "$defs",
        "schemas",
        "responses",
        "parameters",
        "headers",
        "requestBodies",
        "securitySchemes",
        "links",
        "callbacks",
        "paths",
        "content",
        "encoding",
    }
)


def sanitize_draft4_schemas(
    doc: Any,
    counts: dict[str, int] | None = None,
    *,
    _name_map: bool = False,
    _seen: set[tuple[int, bool]] | None = None,
) -> dict[str, int]:
    """Repair draft-4-only schema constructs in place, returning what was changed.

    Applied to every document (converted or natively OpenAPI 3), because the
    constructs are invalid in OAS 3.0 wherever they came from. Keyed on shape, not
    location: only nodes that actually carry a list ``type`` / ``items`` are touched.
    Example payloads, defaults, enum members and ``x-`` extensions are data and are
    left alone.
    """
    counts = {} if counts is None else counts
    # $ref resolution shares one object at every use site; visiting each once keeps a
    # large spec (thousands of uses of one schema) linear instead of exponential.
    seen = set() if _seen is None else _seen
    if isinstance(doc, (dict, list)):
        key_id = (id(doc), _name_map)
        if key_id in seen:
            return counts
        seen.add(key_id)
    if isinstance(doc, dict):
        if not _name_map:
            _sanitize_schema_node(doc, counts)
        for key, value in doc.items():
            if not _name_map and (key in _DATA_KEYS or str(key).startswith("x-")):
                continue
            sanitize_draft4_schemas(
                value, counts, _name_map=not _name_map and key in _NAME_MAP_KEYS, _seen=seen
            )
    elif isinstance(doc, list):
        for value in doc:
            sanitize_draft4_schemas(value, counts, _seen=seen)
    return counts


def normalize_swagger2_to_openapi3(
    doc: dict[str, Any], *, source_url: str | None = None
) -> dict[str, Any]:
    """Convert a Swagger 2.0 document dict to an OpenAPI 3.0.3 dict (subset).

    ``source_url``: where the document was fetched from. Swagger 2.0 says a missing
    ``host`` means "the host serving the documentation", and a missing scheme the
    scheme used to fetch it.
    """
    from urllib.parse import urlparse

    src = copy.deepcopy(doc)
    src["paths"] = _inline_global_refs(src)
    info = src.get("info") or {"title": "converted", "version": "0.0.0"}
    source = urlparse(source_url) if source_url else None
    host = src.get("host") or (source.netloc if source else None)
    base_path = src.get("basePath") or ""
    schemes = list(src.get("schemes") or [source.scheme if source else "https"])
    servers: list[dict[str, Any]] = []
    if host:
        # Several schemes (often [http, https]): prefer the secure one.
        scheme = "https" if "https" in schemes else schemes[0]
        url = f"{scheme}://{host}{base_path}"
        servers.append({"url": url})
    elif base_path:
        servers.append({"url": base_path})

    components: dict[str, Any] = {"schemas": {}, "securitySchemes": {}}
    for name, schema in (src.get("definitions") or {}).items():
        components["schemas"][name] = _rewrite_refs(schema)
    for name, scheme in (src.get("securityDefinitions") or {}).items():
        components["securitySchemes"][name] = _map_security_scheme(name, scheme)

    global_consumes = list(src.get("consumes") or [])
    global_produces = list(src.get("produces") or [])

    paths: dict[str, Any] = {}
    for path, item in (src.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        new_item: dict[str, Any] = {}
        path_params = item.get("parameters") or []
        for method, op in item.items():
            if method == "parameters":
                continue
            if method.lower() not in _HTTP_METHODS_V2 or not isinstance(op, dict):
                new_item[method] = op  # `x-` extensions and `$ref` pass through
                continue
            consumes = list(op.get("consumes") or global_consumes)
            produces = list(op.get("produces") or global_produces)
            # An operation parameter overrides a path-level one with the same name and `in`;
            # concatenating them is a "duplicate parameter" in OpenAPI 3.
            merged: dict[Any, Any] = {}
            for prm in [*path_params, *(op.get("parameters") or [])]:
                key = (prm.get("name"), prm.get("in")) if isinstance(prm, dict) else id(prm)
                merged[key if "$ref" not in (prm or {}) else id(prm)] = prm
            params = list(merged.values())
            kept, request_body = _convert_parameters(params, consumes=consumes)
            new_op = {
                k: v
                for k, v in op.items()
                if k
                not in {
                    "parameters",
                    "consumes",
                    "produces",
                    "responses",
                    # Operation-level `schemes` has no OpenAPI 3 equivalent; the server's
                    # scheme (https preferred) applies.
                    "schemes",
                }
            }
            new_op["parameters"] = kept
            if request_body is not None:
                new_op["requestBody"] = request_body
            new_op["responses"] = _convert_responses(op.get("responses") or {}, produces=produces)
            new_item[method] = _rewrite_refs(new_op)
        paths[path] = new_item

    result: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": info,
        "paths": paths,
        "components": components,
    }
    if servers:
        result["servers"] = servers
    if src.get("security") is not None:
        result["security"] = src["security"]
    if src.get("tags") is not None:
        result["tags"] = src["tags"]
    return _v2_schema_fixups(_strip_nones(_rewrite_refs(result)))


def _v2_schema_fixups(node: Any, *, _name_map: bool = False) -> Any:
    """Schema forms Swagger 2.0 allows and OpenAPI 3.0 does not, rewritten in place.

    * ``discriminator: <property name>`` (a string) → ``discriminator: {propertyName: …}``
    * ``type: file`` (response or property) → ``type: string, format: binary``
    * ``collectionFormat`` inside an Items Object (nested arrays) → removed; it has no
      OpenAPI 3 place, and the outer parameter's style still applies.
    """
    if isinstance(node, dict):
        if not _name_map:
            if isinstance(node.get("discriminator"), str):
                node["discriminator"] = {"propertyName": node["discriminator"]}
            if node.get("type") == "file":
                node["type"] = "string"
                node["format"] = "binary"
            items = node.get("items")
            if isinstance(items, dict):
                items.pop("collectionFormat", None)
        for key, value in node.items():
            if not _name_map and (key in _DATA_KEYS or str(key).startswith("x-")):
                continue
            _v2_schema_fixups(value, _name_map=not _name_map and key in _NAME_MAP_KEYS)
    elif isinstance(node, list):
        for value in node:
            _v2_schema_fixups(value)
    return node

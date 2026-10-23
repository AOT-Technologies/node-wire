#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Shared hardened REST executor for OpenAPI-generated connectors."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Mapping, Optional, Type
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from .base_connector import BaseConnector
from .http_safety import (
    SsrfBlockedError,
    assert_base_url_allowed,
    assert_safe_destination,
    rest_trust_env,
    sanitize_url_for_log,
)

logger = logging.getLogger("runtime.rest")

__all__ = [
    "RestResponseOutput",
    "RestConnector",
    "SsrfBlockedError",
    "encode_param_value",
    "split_params_by_location",
    "validate_static_headers",
]

# RFC 9110 field-name token.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
# A `${VAR}` connectors.yaml substitution left in place because VAR was not set.
_UNRESOLVED_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)(?::[^}]*)?\}")
# Set by the auth provider or the HTTP client; a fixed value here would either be
# overridden or break the request, and credentials belong in `auth`.
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
    }
)


def validate_static_headers(headers: Any) -> Dict[str, str]:
    """Check a ``headers:`` config block and return it as a plain dict.

    Raises ``ValueError`` naming the offending header. Values are literal: credentials
    belong in ``auth``, which also wins over any header set here.
    """
    if not isinstance(headers, Mapping):
        raise ValueError("headers must be a mapping of header name to value")
    out: Dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name):
            raise ValueError(f"header name {name!r} is not a valid HTTP field name")
        if name.lower() in _RESERVED_HEADERS:
            raise ValueError(f"header {name!r} cannot be set in headers")
        if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
            raise ValueError(f"header {name!r} must be a single-line string")
        out[name] = value
    return out


class RestResponseOutput(BaseModel):
    """Generic HTTP response envelope when no typed JSON success schema exists."""

    status_code: int
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Any = None


class RestEnvelopeError(Exception):
    """A 2xx response whose declared success flag reported failure.

    Some APIs answer ``200 OK`` with ``{"ok": false, "error": "..."}`` instead of
    an HTTP error status. Nothing above this layer can tell that apart from a
    successful call, so the connector would report success for a failed request.
    Raised only when the caller passes ``envelope_ok_field`` — see
    :meth:`RestConnector.execute_rest`.
    """

    def __init__(self, field: str, payload: Dict[str, Any]) -> None:
        self.field = field
        self.payload = payload
        detail = payload.get("error")
        # `error` is this envelope convention's companion to the flag. Truncated:
        # some APIs return a whole nested object there, and this string reaches logs.
        suffix = f": {str(detail)[:200]}" if isinstance(detail, str) and detail else ""
        super().__init__(f"API reported {field}=false{suffix}")


def _check_envelope(payload: Any, field: Optional[str]) -> None:
    """Raise :class:`RestEnvelopeError` when ``payload[field]`` is literally ``false``.

    A missing field is not a failure: the flag is only meaningful when the API
    actually sent it, and absence means the response said nothing either way.
    """
    if not field or not isinstance(payload, dict):
        return
    if payload.get(field) is False:
        raise RestEnvelopeError(field, payload)


def _field_extra(model: BaseModel, name: str) -> dict[str, Any]:
    field = type(model).model_fields.get(name)
    if field is None:
        return {}
    extra = field.json_schema_extra
    if callable(extra):
        return {}
    if isinstance(extra, dict):
        return extra
    return {}


@dataclass
class _SplitParams:
    """Every wire location of one call, keyed by wire name: ``(value, field extras)``."""

    path: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    query: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    header: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    cookie: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    # OpenAPI 3.2 `in: querystring`: one value that is the whole query string.
    querystring: Optional[tuple[Any, dict[str, Any]]] = None
    body: Any | None = None
    media_type: str | None = None
    body_format: str | None = None
    part_formats: dict[str, str] = field(default_factory=dict)
    part_types: dict[str, str] = field(default_factory=dict)


def _split_params(params: BaseModel) -> _SplitParams:
    out = _SplitParams()
    data = params.model_dump(by_alias=False, exclude_none=True)
    body_props, props_media = _collect_body_properties(params, data)
    for name, value in data.items():
        if name == "action":
            continue
        extra = _field_extra(params, name)
        loc = extra.get("nw_in")
        wire = extra.get("nw_wire_name") or name
        if loc in ("path", "query", "header", "cookie"):
            getattr(out, loc)[wire] = (value, extra)
        elif loc == "querystring":
            out.querystring = (value, extra)
        elif loc == "body":
            out.body = getattr(params, name, value)
            out.media_type = extra.get("nw_media_type") or "application/json"
            out.body_format = extra.get("nw_format")
        # body_property fields were collected above. Unknown / missing nw_in:
        # ignore (action discriminator already skipped).
    if body_props is not None:
        out.body, out.media_type = body_props, props_media
        for name in type(params).model_fields:
            extra = _field_extra(params, name)
            if extra.get("nw_in") != "body_property":
                continue
            wire = extra.get("nw_wire_name") or name
            if extra.get("nw_format"):
                out.part_formats[wire] = extra["nw_format"]
            if extra.get("nw_part_content_type"):
                out.part_types[wire] = extra["nw_part_content_type"]
    return out


def split_params_by_location(
    params: BaseModel,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Any | None,
    str | None,
]:
    """Split a typed input model into path/query/header/body using ``nw_in`` metadata.

    Returns ``(path, query, header, body, media_type)``; each parameter is packed as
    ``(value, style, explode)``. Cookie and ``querystring`` parameters are not part of
    this tuple (see :meth:`RestConnector.execute_rest`, which sends them).

    The body comes from one of two field shapes, never both:

    * ``nw_in="body"`` — a single field holding the whole body (hand-written
      connectors, and generated ones whose body is not an object with declared
      properties).
    * ``nw_in="body_property"`` — one field per property of an object body, keyed
      on the wire by ``nw_wire_name``. Generated connectors use this so the call
      contract is flat. The body is sent even when no property is set (``{}``):
      the operation declares one, and an empty object is valid where a missing
      body may not be.
    """
    split = _split_params(params)

    def _pack(group: dict[str, tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
        return {
            wire: (value, extra.get("nw_style"), extra.get("nw_explode"))
            for wire, (value, extra) in group.items()
        }

    return _pack(split.path), _pack(split.query), _pack(split.header), split.body, split.media_type


def _collect_body_properties(
    params: BaseModel, data: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Assemble ``nw_in="body_property"`` fields into a body dict, or ``(None, None)``.

    Walks the model's declared fields, not just the set ones, so a model with only
    unset optional properties still yields ``{}``. Raises ``ValueError`` for a
    model that is malformed as generated code: properties disagreeing on media
    type, or a whole-body field declared alongside them.
    """
    body: dict[str, Any] | None = None
    media: str | None = None
    has_whole_body = False
    for name in type(params).model_fields:
        extra = _field_extra(params, name)
        loc = extra.get("nw_in")
        if loc == "body":
            has_whole_body = True
            continue
        if loc != "body_property":
            continue
        field_media = extra.get("nw_media_type") or "application/json"
        if media is not None and field_media != media:
            raise ValueError(
                f"{type(params).__name__}: body_property fields disagree on media type "
                f"({media!r} vs {field_media!r})"
            )
        media = field_media
        if body is None:
            body = {}
        if name in data:
            body[extra.get("nw_wire_name") or name] = data[name]
    if body is not None and has_whole_body:
        raise ValueError(
            f"{type(params).__name__}: declares both a whole-body field (nw_in='body') "
            "and body_property fields"
        )
    return body, media


# RFC 3986 reserved characters a parameter with `allowReserved: true` may send as-is.
# `&`, `=` and `#` stay encoded: they would split the query string or end the URL.
_ALLOW_RESERVED_SAFE = ":/?[]@!$'()*+,;"


def _scalar(value: Any) -> str:
    """One value as OpenAPI serializes it: JSON literals for booleans and nested data."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _content_value(value: Any, media_type: Optional[str]) -> Any:
    """A parameter declared with ``content`` (JSON) is sent as its JSON text."""
    if media_type and "json" in media_type.lower():
        return json.dumps(value, separators=(",", ":"))
    return value


def encode_param_value(
    value: Any,
    *,
    style: str | None,
    explode: bool | None,
    location: str,
    name: str | None = None,
    allow_reserved: bool = False,
) -> str | list[tuple[str, str]] | list[str]:
    """Encode a parameter value per OpenAPI ``style`` / ``explode``.

    Path values come back percent-encoded (separators kept); header and cookie values
    raw; query values unencoded, as a string, a list of values (one key repeated) or a
    list of ``(key, value)`` pairs. Styles: path ``simple`` / ``label`` / ``matrix``;
    query ``form`` / ``spaceDelimited`` / ``pipeDelimited`` / ``deepObject``; header
    ``simple``; cookie ``form``. ``name`` is needed for ``matrix`` and ``deepObject``.
    """
    key = name or ""
    is_list = isinstance(value, (list, tuple))
    is_dict = isinstance(value, dict)

    if location in ("path", "header"):
        safe = _ALLOW_RESERVED_SAFE if allow_reserved else ""
        enc = (lambda v: quote(_scalar(v), safe=safe)) if location == "path" else _scalar
        style = style or "simple"
        explode = bool(explode)
        if location == "header" and style != "simple":
            raise ValueError(f"unsupported header style: {style}")
        if style == "simple":
            if is_list:
                return ",".join(enc(v) for v in value)
            if is_dict:
                sep = "=" if explode else ","
                return ",".join(f"{enc(k)}{sep}{enc(v)}" for k, v in value.items())
            return enc(value)
        if style == "label":
            if is_list:
                return "." + ("." if explode else ",").join(enc(v) for v in value)
            if is_dict:
                if explode:
                    return "." + ".".join(f"{enc(k)}={enc(v)}" for k, v in value.items())
                return "." + ",".join(f"{enc(k)},{enc(v)}" for k, v in value.items())
            return "." + enc(value)
        if style == "matrix":
            if is_list:
                if explode:
                    return "".join(f";{key}={enc(v)}" for v in value)
                return f";{key}=" + ",".join(enc(v) for v in value)
            if is_dict:
                if explode:
                    return "".join(f";{enc(k)}={enc(v)}" for k, v in value.items())
                return f";{key}=" + ",".join(f"{enc(k)},{enc(v)}" for k, v in value.items())
            return f";{key}={enc(value)}"
        raise ValueError(f"unsupported path style: {style}")

    if location == "cookie":
        if is_list:
            return ",".join(_scalar(v) for v in value)
        if is_dict:
            return ",".join(f"{_scalar(k)},{_scalar(v)}" for k, v in value.items())
        return _scalar(value)

    # query
    style = style or "form"
    explode = True if explode is None else bool(explode)
    if style == "form":
        if is_list:
            if explode:
                return [_scalar(v) for v in value]
            return ",".join(_scalar(v) for v in value)
        if is_dict:
            if explode:
                return [(str(k), _scalar(v)) for k, v in value.items()]
            return ",".join(f"{k},{_scalar(v)}" for k, v in value.items())
        return _scalar(value)
    if style in ("spaceDelimited", "pipeDelimited"):
        sep = " " if style == "spaceDelimited" else "|"
        if is_list:
            if explode:
                return [_scalar(v) for v in value]
            return sep.join(_scalar(v) for v in value)
        if is_dict:
            return sep.join(f"{k}{sep}{_scalar(v)}" for k, v in value.items())
        return _scalar(value)
    if style == "deepObject":
        if not is_dict:
            return _scalar(value)
        return [(f"{key}[{k}]", _scalar(v)) for k, v in value.items()]
    raise ValueError(f"unsupported query style: {style}")


def _apply_path_template(template: str, path_params: Mapping[str, Any]) -> str:
    result = template
    for name, packed in path_params.items():
        value, style, explode = packed[:3]
        allow_reserved = bool(packed[3]) if len(packed) > 3 else False
        encoded = encode_param_value(
            value,
            style=style,
            explode=explode,
            location="path",
            name=name,
            allow_reserved=allow_reserved,
        )
        assert isinstance(encoded, str)
        result = result.replace("{" + name + "}", encoded)
    return result


def _build_query_pairs(query_params: Mapping[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, packed in query_params.items():
        value, style, explode = packed[:3]
        encoded = encode_param_value(
            value, style=style, explode=explode, location="query", name=name
        )
        if isinstance(encoded, list):
            for item in encoded:
                if isinstance(item, tuple):
                    pairs.append((str(item[0]), str(item[1])))
                else:
                    pairs.append((name, str(item)))
        else:
            pairs.append((name, str(encoded)))
    return pairs


def _build_headers(header_params: Mapping[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, packed in header_params.items():
        value, style, explode = packed[:3]
        encoded = encode_param_value(value, style=style, explode=explode, location="header")
        headers[name] = str(encoded)
    return headers


def _cookie_header(cookie_params: Mapping[str, Any]) -> Optional[str]:
    """``Cookie: a=1; b=2`` from cookie parameters (values percent-encoded)."""
    parts: list[str] = []
    for name, (value, extra) in cookie_params.items():
        value = _content_value(value, extra.get("nw_media_type"))
        # OAS 3.2 `style: cookie` sends values as-is; `form` (the default) percent-encodes.
        raw = extra.get("nw_style") == "cookie"
        enc = (lambda v: v) if raw else (lambda v: quote(v, safe=","))
        explode = extra.get("nw_explode")
        explode = True if explode is None else bool(explode)  # form's default is explode
        if isinstance(value, (list, tuple)) and explode:
            parts.extend(f"{name}={enc(_scalar(v))}" for v in value)
            continue
        if isinstance(value, dict) and explode:
            parts.extend(f"{enc(str(k))}={enc(_scalar(v))}" for k, v in value.items())
            continue
        encoded = encode_param_value(value, style="form", explode=False, location="cookie")
        parts.append(f"{name}={enc(str(encoded))}")
    return "; ".join(parts) or None


def _query_string(
    query_params: Mapping[str, tuple[Any, dict[str, Any]]],
    querystring: Optional[tuple[Any, dict[str, Any]]],
) -> str:
    """The encoded query string: each parameter per its style, ``allowReserved`` honoured."""
    pieces: list[str] = []
    for name, (value, extra) in query_params.items():
        value = _content_value(value, extra.get("nw_media_type"))
        safe = _ALLOW_RESERVED_SAFE if extra.get("nw_allow_reserved") else ""
        for key, item in _build_query_pairs(
            {name: (value, extra.get("nw_style"), extra.get("nw_explode"))}
        ):
            pieces.append(f"{quote(key, safe='[]')}={quote(item, safe=safe)}")
    if querystring is not None:
        value, extra = querystring
        media = (extra.get("nw_media_type") or "").lower()
        if isinstance(value, dict) and "json" not in media:
            pieces.extend(
                f"{quote(str(k), safe='')}={quote(_scalar(v), safe='')}" for k, v in value.items()
            )
        else:
            text = _content_value(value, media) if not isinstance(value, str) else value
            pieces.append(text if "json" not in media else quote(str(text), safe=""))
    return "&".join(pieces)


def _body_to_jsonable(body: Any) -> Any:
    if isinstance(body, BaseModel):
        return body.model_dump(by_alias=True, exclude_none=True)
    return body


def _form_field_value(value: Any) -> Optional[str]:
    """Render one value for an ``x-www-form-urlencoded`` / multipart form field.

    ``str()`` is wrong for three JSON types that reach here routinely:

    * ``bool`` renders as ``True``/``False``; every API this layer talks to reads
      form values as JSON-ish text and expects ``true``/``false``.
    * ``dict`` / ``list`` render as a Python repr with single quotes, which is not
      JSON — Slack rejects a ``blocks`` field encoded that way with
      ``invalid_arguments``.
    * ``None`` renders as the string ``"None"``, i.e. an omitted optional field
      becomes the literal text. Returning ``None`` here drops the field instead.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _decode_binary(value: str, fmt: str) -> bytes:
    """Base64 (``binary`` / ``byte``) or base64url (3.1 ``contentEncoding``), padding optional."""
    padded = value + "=" * (-len(value) % 4)
    if fmt == "base64url":
        return base64.urlsafe_b64decode(padded)
    return base64.b64decode(padded, validate=False)


def _is_textual(media_type: str) -> bool:
    return media_type.startswith("text/") or "xml" in media_type or "json" in media_type


def _encode_request_body(
    body: Any,
    media_type: str | None,
    *,
    body_format: str | None = None,
    part_formats: Mapping[str, str] | None = None,
    part_types: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return kwargs for httpx.request (json/data/files/content).

    ``body_format`` / ``part_formats``: the schema ``format`` of a whole body or of each
    multipart property (``nw_format``). ``binary`` / ``byte`` values arrive base64-encoded
    and are decoded; anything else is sent as text.
    """
    mt = (media_type or "application/json").lower()
    part_formats = part_formats or {}
    part_types = part_types or {}
    if body is None:
        return {}

    if mt == "application/json" or mt.endswith("+json"):
        return {"json": _body_to_jsonable(body)}

    if mt == "application/x-www-form-urlencoded":
        data = _body_to_jsonable(body)
        if not isinstance(data, dict):
            raise ValueError("form-urlencoded body must be an object")
        encoded: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, list) and v and all(not isinstance(i, (dict, list)) for i in v):
                # OpenAPI's default for a form array is style form, explode: repeated keys.
                encoded[str(k)] = [_form_field_value(i) for i in v]
            else:
                encoded[str(k)] = _form_field_value(v)
        return {"data": {k: v for k, v in encoded.items() if v is not None}}

    if mt.startswith("multipart/"):
        data = _body_to_jsonable(body)
        if not isinstance(data, dict):
            raise ValueError("multipart body must be an object")
        files: list[tuple[str, Any]] = []
        form_data: dict[str, str] = {}
        for key, val in data.items():
            if isinstance(val, str) and part_formats.get(key) in ("binary", "byte", "base64url"):
                raw = _decode_binary(val, part_formats[key])
                # The encoding object's contentType for this part, when the spec gives one.
                ctype = part_types.get(key)
                files.append((key, (key, raw, ctype) if ctype else (key, raw)))
                continue
            if isinstance(val, str) and key not in part_formats and _looks_base64(val):
                try:
                    raw = base64.b64decode(val, validate=False)
                    files.append((key, (key, raw)))
                    continue
                except Exception as exc:  # noqa: BLE001
                    # Heuristic match, not confirmed base64 — fall back to string field.
                    logger.debug("base64 decode failed for field %r: %s", key, exc)
            if isinstance(val, (bytes, bytearray)):
                files.append((key, (key, bytes(val))))
            else:
                rendered = _form_field_value(val)
                if rendered is not None:
                    form_data[key] = rendered
        kwargs: dict[str, Any] = {}
        if files:
            if form_data:
                kwargs["data"] = form_data
            kwargs["files"] = files
        elif form_data:
            # httpx only sends multipart when there is a file part; without one it would
            # silently send this body urlencoded. Plain fields as (None, value) parts.
            kwargs["files"] = [(k, (None, v)) for k, v in form_data.items()]
        return kwargs

    if isinstance(body, str) and body_format in ("binary", "byte", "base64url"):
        return {"content": _decode_binary(body, body_format)}

    if _is_textual(mt) and body_format not in ("binary", "byte"):
        # text/*, XML and other non-JSON-object textual bodies: the caller passes the text.
        if isinstance(body, (bytes, bytearray)):
            return {"content": bytes(body)}
        if isinstance(body, str):
            return {"content": body.encode("utf-8")}
        return {"content": _scalar(_body_to_jsonable(body)).encode("utf-8")}

    # binary / octet-stream
    if isinstance(body, (bytes, bytearray)):
        return {"content": bytes(body)}
    if isinstance(body, str):
        try:
            return {"content": base64.b64decode(body, validate=False)}
        except Exception:  # noqa: BLE001
            return {"content": body.encode("utf-8")}
    if isinstance(body, BaseModel):
        dumped = body.model_dump(by_alias=True, exclude_none=True)
        # single binary property
        if isinstance(dumped, dict) and len(dumped) == 1:
            only = next(iter(dumped.values()))
            if isinstance(only, str):
                return {"content": base64.b64decode(only, validate=False)}
        return {"content": str(dumped).encode("utf-8")}
    return {"content": str(body).encode("utf-8")}


def _looks_base64(value: str) -> bool:
    """Conservative base64 heuristic for multipart binary fields.

    Requires substantial length and padding so short form-field strings that
    merely happen to use the base64 alphabet (e.g. ``abcdefgh``) are not
    silently promoted to file uploads.
    """
    if len(value) < 64 or len(value) % 4 != 0:
        return False
    if "=" not in value[-3:]:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value))


class RestConnector(BaseConnector):
    """Base class for OpenAPI-generated REST connectors.

    Subclasses set ``default_base_url`` (baked at generation time). Runtime
    ``connectors.yaml`` ``base_url`` overrides via the factory-injected
    ``base_url=`` constructor argument. ``headers=`` (``connectors.yaml``
    ``headers:``) are sent on every request; see :meth:`resolve_static_headers`.
    """

    _nw_abstract_base: ClassVar[bool] = True
    default_base_url: ClassVar[str] = ""
    output_model: ClassVar[Type[BaseModel]] = RestResponseOutput

    def __init__(
        self,
        *,
        secret_provider: Any = None,
        policy_hook: Any = None,
        auth_provider: Any = None,
        auth_providers: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        base_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
        config_name: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(
            secret_provider=secret_provider,
            policy_hook=policy_hook,
            auth_provider=auth_provider,
            auth_providers=auth_providers,
            config=config,
            tenant_id=tenant_id,
            config_name=config_name,
        )
        self._base_url_override = base_url
        self._static_headers = validate_static_headers(headers or {})

    def _auth_providers_for(self, auth_scheme: Optional[str | tuple[str, ...]]) -> list[Any]:
        if isinstance(auth_scheme, (tuple, list)):
            return [self.resolve_auth_provider(name) for name in auth_scheme]
        return [self.resolve_auth_provider(auth_scheme)]

    def resolve_static_headers(self) -> Dict[str, str]:
        """The configured ``headers:``, refusing a ``${VAR}`` that was never substituted.

        ``connectors.yaml`` replaces ``${VAR}`` from the environment when it loads; a value
        still holding one means VAR was unset (or the value came from a tenant config, which
        is not substituted), and sending the placeholder upstream would only come back as an
        unexplained auth error.
        """
        for name, value in self._static_headers.items():
            unresolved = _UNRESOLVED_VAR.search(value)
            if unresolved:
                var = unresolved.group(1)
                raise RuntimeError(
                    f"{type(self).__name__}: header {name!r} still holds ${{{var}}}: set {var} "
                    "in the environment before connectors.yaml loads, or give the value itself "
                    "in the tenant config (tenants.yaml values are not substituted)"
                )
        return dict(self._static_headers)

    def resolve_base_url(self) -> str:
        if self._base_url_override and str(self._base_url_override).strip():
            override = str(self._base_url_override).strip()
            baked = str(getattr(type(self), "default_base_url", "") or "")
            assert_base_url_allowed(override, baked_base_url=baked)
            return override.rstrip("/")
        baked = getattr(type(self), "default_base_url", "") or ""
        if not baked:
            raise RuntimeError(
                f"{type(self).__name__}: no base_url configured "
                "(set connectors.yaml base_url or default_base_url)"
            )
        return str(baked).rstrip("/")

    async def execute_rest(
        self,
        method: str,
        path_template: str,
        params: BaseModel,
        *,
        output_model: Type[BaseModel],
        trace_id: str,
        auth: bool = True,
        auth_scheme: Optional[str | tuple[str, ...]] = None,
        envelope_ok_field: Optional[str] = None,
        server_url: Optional[str] = None,
    ) -> Any:
        """Execute an HTTP call using ``nw_in`` field metadata on ``params``.

        ``auth_scheme`` names the scheme to authenticate with (``None``: the connector
        default). A tuple names several that the operation needs together (an OpenAPI
        security requirement listing more than one scheme); each adds its headers or
        query parameters.

        ``server_url`` is an operation's own server (OpenAPI operation- or path-level
        ``servers``), used instead of the spec's base URL for this call. An operator
        ``base_url`` override still wins over it, so a connector pointed at a proxy or
        gateway never sends its credentials straight to the spec's host. Egress checks
        apply to it like to any other URL.

        ``envelope_ok_field`` names a boolean field in the JSON body that the API
        sets to ``false`` to report failure while still answering ``2xx``. When
        given and the field comes back ``false``, raises :class:`RestEnvelopeError`
        instead of returning a response that looks successful. Defaults to off.
        """
        has_override = bool(self._base_url_override and str(self._base_url_override).strip())
        if server_url and not has_override:
            base = server_url.rstrip("/")
        else:
            base = self.resolve_base_url()
        split = _split_params(params)
        body, media_type = split.body, split.media_type
        path_params = {
            wire: (
                _content_value(value, extra.get("nw_media_type")),
                extra.get("nw_style"),
                extra.get("nw_explode"),
                extra.get("nw_allow_reserved"),
            )
            for wire, (value, extra) in split.path.items()
        }
        path = _apply_path_template(path_template, path_params)
        if not path.startswith("/"):
            path = "/" + path
        url = f"{base}{path}"

        headers = _build_headers(
            {
                wire: (
                    _content_value(value, extra.get("nw_media_type")),
                    extra.get("nw_style"),
                    extra.get("nw_explode"),
                )
                for wire, (value, extra) in split.header.items()
            }
        )
        cookie = _cookie_header(split.cookie)
        if cookie:
            headers["Cookie"] = cookie
        # Operator config beats caller input; auth (below) beats both.
        headers.update(self.resolve_static_headers())

        query_params = dict(split.query)
        if auth:
            for provider in self._auth_providers_for(auth_scheme):
                headers.update(await provider.get_headers())
                query_auth = await provider.get_query_params()
                # Auth wins on key collision.
                for key, value in (query_auth or {}).items():
                    query_params[key] = (value, {"nw_style": "form", "nw_explode": True})

        query = _query_string(query_params, split.querystring)
        if query:
            url = f"{url}?{query}"

        body_kwargs = _encode_request_body(
            body,
            media_type,
            body_format=split.body_format,
            part_formats=split.part_formats,
            part_types=split.part_types,
        )
        if body is not None and media_type and "Content-Type" not in headers:
            if media_type == "application/json" or media_type.endswith("+json"):
                headers.setdefault("Content-Type", "application/json")
            elif media_type == "application/x-www-form-urlencoded":
                headers.setdefault("Content-Type", media_type)
            elif not media_type.startswith("multipart/"):
                headers.setdefault("Content-Type", media_type)

        safe_url = sanitize_url_for_log(url)
        logger.info(
            "Executing REST call",
            extra={
                "trace_id": trace_id,
                "connector_id": getattr(self, "connector_id", None),
                "method": method,
                "url": safe_url,
            },
        )

        await assert_safe_destination(url)

        timeout = float(os.getenv("NW_TIMEOUT", "30.0"))
        trust_env = rest_trust_env()
        async with httpx.AsyncClient(
            timeout=timeout, trust_env=trust_env, follow_redirects=False
        ) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                timeout=timeout,
                **body_kwargs,
            )

        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            response.raise_for_status()

        resp_headers = {k: v for k, v in response.headers.items()}

        if output_model is RestResponseOutput or (
            isinstance(output_model, type) and issubclass(output_model, RestResponseOutput)
        ):
            body_val: Any
            ctype = (response.headers.get("content-type") or "").lower()
            if "json" in ctype and response.content:
                try:
                    body_val = response.json()
                except Exception:  # noqa: BLE001
                    body_val = response.text
            else:
                body_val = response.text if response.content else None
            _check_envelope(body_val, envelope_ok_field)
            return RestResponseOutput(
                status_code=response.status_code,
                headers=resp_headers,
                body=body_val,
            )

        if not response.content:
            return output_model.model_validate({})

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Expected JSON response for {output_model.__name__}, got non-JSON body"
            ) from exc
        _check_envelope(payload, envelope_ok_field)
        return output_model.model_validate(payload)

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
from typing import Any, ClassVar, Dict, Mapping, Optional, Type
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from .base_connector import BaseConnector
from .http_safety import (
    SsrfBlockedError,
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
]


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
    path: dict[str, Any] = {}
    query: dict[str, Any] = {}
    header: dict[str, Any] = {}
    body: Any | None = None
    media_type: str | None = None

    data = params.model_dump(by_alias=False, exclude_none=True)
    body_props, props_media = _collect_body_properties(params, data)
    for name, value in data.items():
        if name == "action":
            continue
        extra = _field_extra(params, name)
        loc = extra.get("nw_in")
        wire = extra.get("nw_wire_name") or name
        style = extra.get("nw_style")
        explode = extra.get("nw_explode")

        if loc == "path":
            path[wire] = (value, style, explode)
        elif loc == "query":
            query[wire] = (value, style, explode)
        elif loc == "header":
            header[wire] = (value, style, explode)
        elif loc == "body":
            body = getattr(params, name, value)
            media_type = extra.get("nw_media_type") or "application/json"
        # body_property fields were collected above. Unknown / missing nw_in:
        # ignore (action discriminator already skipped).

    if body_props is not None:
        body, media_type = body_props, props_media

    return path, query, header, body, media_type


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


def encode_param_value(
    value: Any,
    *,
    style: str | None,
    explode: bool | None,
    location: str,
) -> str | list[tuple[str, str]] | list[str]:
    """Encode a path/query/header value per OpenAPI style/explode (v1 subset)."""
    if location == "path":
        style = style or "simple"
        explode = False if explode is None else bool(explode)
        if style != "simple":
            raise ValueError(f"unsupported path style: {style}")
        if isinstance(value, (list, tuple)):
            return quote(",".join(str(v) for v in value), safe="")
        if isinstance(value, dict):
            if explode:
                return quote(",".join(f"{k}={v}" for k, v in value.items()), safe="")
            return quote(",".join(f"{k},{v}" for k, v in value.items()), safe="")
        return quote(str(value), safe="")

    if location == "header":
        style = style or "simple"
        if style != "simple":
            raise ValueError(f"unsupported header style: {style}")
        if isinstance(value, (list, tuple)):
            return ",".join(str(v) for v in value)
        return str(value)

    # query — form style
    style = style or "form"
    explode = True if explode is None else bool(explode)
    if style != "form":
        raise ValueError(f"unsupported query style: {style}")
    if isinstance(value, (list, tuple)):
        if explode:
            return [str(v) for v in value]
        return ",".join(str(v) for v in value)
    if isinstance(value, dict):
        if explode:
            return [(str(k), str(v)) for k, v in value.items()]
        return ",".join(f"{k},{v}" for k, v in value.items())
    return str(value)


def _apply_path_template(template: str, path_params: Mapping[str, Any]) -> str:
    result = template
    for name, packed in path_params.items():
        value, style, explode = packed
        encoded = encode_param_value(value, style=style, explode=explode, location="path")
        assert isinstance(encoded, str)
        result = result.replace("{" + name + "}", encoded)
    return result


def _build_query_pairs(query_params: Mapping[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, packed in query_params.items():
        value, style, explode = packed
        encoded = encode_param_value(value, style=style, explode=explode, location="query")
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
        value, style, explode = packed
        encoded = encode_param_value(value, style=style, explode=explode, location="header")
        headers[name] = str(encoded)
    return headers


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


def _encode_request_body(
    body: Any,
    media_type: str | None,
) -> dict[str, Any]:
    """Return kwargs for httpx.request (json/data/files/content)."""
    mt = (media_type or "application/json").lower()
    if body is None:
        return {}

    if mt == "application/json" or mt.endswith("+json"):
        return {"json": _body_to_jsonable(body)}

    if mt == "application/x-www-form-urlencoded":
        data = _body_to_jsonable(body)
        if not isinstance(data, dict):
            raise ValueError("form-urlencoded body must be an object")
        encoded = {str(k): _form_field_value(v) for k, v in data.items()}
        return {"data": {k: v for k, v in encoded.items() if v is not None}}

    if mt.startswith("multipart/"):
        data = _body_to_jsonable(body)
        if not isinstance(data, dict):
            raise ValueError("multipart body must be an object")
        files: list[tuple[str, Any]] = []
        form_data: dict[str, str] = {}
        for key, val in data.items():
            if isinstance(val, str) and _looks_base64(val):
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
        if form_data:
            kwargs["data"] = form_data
        if files:
            kwargs["files"] = files
        return kwargs

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
    import re

    if len(value) < 64 or len(value) % 4 != 0:
        return False
    if "=" not in value[-3:]:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value))


class RestConnector(BaseConnector):
    """Base class for OpenAPI-generated REST connectors.

    Subclasses set ``default_base_url`` (baked at generation time). Runtime
    ``connectors.yaml`` ``base_url`` overrides via the factory-injected
    ``base_url=`` constructor argument.
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

    def resolve_base_url(self) -> str:
        if self._base_url_override and str(self._base_url_override).strip():
            return str(self._base_url_override).rstrip("/")
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
        auth_scheme: Optional[str] = None,
        envelope_ok_field: Optional[str] = None,
    ) -> Any:
        """Execute an HTTP call using ``nw_in`` field metadata on ``params``.

        ``envelope_ok_field`` names a boolean field in the JSON body that the API
        sets to ``false`` to report failure while still answering ``2xx``. When
        given and the field comes back ``false``, raises :class:`RestEnvelopeError`
        instead of returning a response that looks successful. Defaults to off.
        """
        base = self.resolve_base_url()
        path_params, query_params, header_params, body, media_type = split_params_by_location(
            params
        )
        path = _apply_path_template(path_template, path_params)
        if not path.startswith("/"):
            path = "/" + path
        url = f"{base}{path}"

        query_pairs = _build_query_pairs(query_params)
        headers = _build_headers(header_params)

        if auth:
            provider = self.resolve_auth_provider(auth_scheme)
            auth_headers = await provider.get_headers()
            headers.update(auth_headers)
            query_auth = await provider.get_query_params()
            if query_auth:
                # Auth wins on key collision.
                query_pairs = [(k, v) for k, v in query_pairs if k not in query_auth]
                query_pairs.extend((k, v) for k, v in query_auth.items())

        body_kwargs = _encode_request_body(body, media_type)
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
                # tuple[...] is covariant; list[...] is not — satisfies httpx QueryParamTypes.
                params=tuple(query_pairs) if query_pairs else None,
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

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""OpenAPI / Swagger load, 2.0 normalize, remote-$ref policy, prance validate."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from nw_connector_builder.normalize_v2 import normalize_swagger2_to_openapi3
from nw_connector_builder.refs import collect_remote_refs

logger = logging.getLogger(__name__)


class SpecLoadError(Exception):
    """Hard failure while loading or validating a spec."""

    def __init__(self, message: str, *, meta: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.meta = meta or {}


def _is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"}


async def _fetch_url_bytes(url: str) -> bytes:
    from node_wire_runtime.http_safety import assert_safe_destination

    await assert_safe_destination(url)
    async with httpx.AsyncClient(timeout=60.0, trust_env=False, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _fetch_url_bytes_sync(url: str) -> bytes:
    import asyncio

    return asyncio.run(_fetch_url_bytes(url))


def load_raw_document(source: str) -> tuple[dict[str, Any], str, bool]:
    """Load bytes from path or URL → raw dict.

    Returns ``(doc, origin_label, from_url)``.
    """
    if _is_url(source):
        logger.info("Fetching OpenAPI spec from URL", extra={"url": source})
        raw = _fetch_url_bytes_sync(source)
        origin = source
        from_url = True
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise SpecLoadError(f"Spec file not found: {path}")
        raw = path.read_bytes()
        origin = str(path)
        from_url = False

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecLoadError("Spec is not valid UTF-8") from exc

    try:
        doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        raise SpecLoadError(f"Failed to parse YAML/JSON: {exc}") from exc

    if not isinstance(doc, dict):
        raise SpecLoadError("Spec root must be a mapping/object")
    return doc, origin, from_url


def detect_version(doc: dict[str, Any]) -> str:
    if "swagger" in doc and str(doc.get("swagger", "")).startswith("2"):
        return "2.0"
    oa = doc.get("openapi")
    if isinstance(oa, str) and oa.startswith("3."):
        return oa
    raise SpecLoadError(
        "Unrecognized OpenAPI version (expected swagger: '2.0' or openapi: '3.x')"
    )


def resolve_and_validate(
    doc: dict[str, Any],
    *,
    from_url: bool,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Apply remote-$ref policy then resolve + validate.

    Uses ``prance.util.resolver.RefResolver`` (not ``ResolvingParser(spec_string=…,
    url=…)``, which breaks on ``ParseResult`` in this prance version) so local
    relative-file ``$ref``s resolve when ``base_url`` is the absolute path of the
    root file. Absolute remote ``$ref``s are rejected by the pre-scan first.
    """
    remotes = collect_remote_refs(doc, from_url=from_url)
    if remotes:
        listed = "\n".join(f"  - {r}" for r in remotes)
        raise SpecLoadError(f"Remote $ref values are not allowed:\n{listed}")

    try:
        from openapi_spec_validator import validate
        from prance.util.resolver import RefResolver
    except ImportError as exc:
        raise SpecLoadError(
            "prance[osv] / openapi-spec-validator required "
            "(pip install 'prance[osv]')"
        ) from exc

    if not base_url:
        raise SpecLoadError(
            "Internal error: RefResolver requires a base_url for fragment $ref resolution"
        )

    try:
        resolver = RefResolver(doc, url=base_url)
        resolver.resolve_references()
        resolved = resolver.specs
        if not isinstance(resolved, dict):
            raise SpecLoadError("ref resolver returned a non-object specification")
        validate(resolved)
    except SpecLoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpecLoadError(f"OpenAPI validation/deref failed: {exc}") from exc

    return resolved


def load_openapi_document(
    source: str,
    *,
    base_url_override: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Full §3 pipeline.

    Returns ``(resolved_3x_doc, meta)`` where meta has version, origin, content_hash, etc.
    """
    import hashlib

    doc, origin, from_url = load_raw_document(source)
    version = detect_version(doc)
    content_hash = hashlib.sha256(
        json.dumps(doc, sort_keys=True, default=str).encode()
    ).hexdigest()

    if version == "2.0":
        logger.info("Normalizing Swagger 2.0 → OpenAPI 3.0")
        doc = normalize_swagger2_to_openapi3(doc)

    # RefResolver needs a base URL even for same-document ``#/…`` fragments.
    # URL-fetched specs: use the origin http(s) URL (remote absolute/relative
    # refs are already rejected by the pre-scan). Local specs: filesystem path.
    base_url = origin if from_url else str(Path(origin).resolve())
    meta = {
        "origin": origin,
        "from_url": from_url,
        "spec_version": version,
        "content_hash": content_hash,
        "base_url_override": base_url_override,
    }
    try:
        resolved = resolve_and_validate(doc, from_url=from_url, base_url=base_url)
    except SpecLoadError as exc:
        raise SpecLoadError(str(exc), meta=meta) from exc
    return resolved, meta

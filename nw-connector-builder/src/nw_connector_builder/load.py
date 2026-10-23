# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""OpenAPI / Swagger load, 2.0 normalize, remote-$ref policy, prance validate."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from nw_connector_builder.normalize_v2 import (
    normalize_swagger2_to_openapi3,
    sanitize_draft4_schemas,
)
from nw_connector_builder.refs import collect_remote_refs

logger = logging.getLogger(__name__)


class SpecLoadError(Exception):
    """Hard failure while loading or validating a spec."""

    def __init__(self, message: str, *, meta: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.meta = meta or {}


class _SpecLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # type: ignore[misc]
    """YAML loader that reads what a JSON parser would.

    PyYAML follows YAML 1.1, where ``yes`` / ``no`` / ``on`` / ``off`` are booleans and
    unquoted dates become ``datetime``. OpenAPI documents are JSON data, so those must stay
    strings: GitHub's spec has an emoji map keyed ``yes`` and ``no`` next to string keys.
    """


_SpecLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_SpecLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _string_keys(node: Any) -> Any:
    """JSON object keys are strings; YAML lets ``200:`` through as an int."""
    if isinstance(node, dict):
        return {str(k): _string_keys(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_string_keys(v) for v in node]
    return node


def drop_extensions(node: Any, *, _name_map: bool = False) -> None:  # noqa: C901
    """Remove ``x-`` specification extensions in place, before ``$ref`` resolution.

    The generator reads none of them (its own ``x-nw-`` markers excepted), and resolving
    everything inside them is how a
    dangling ``$ref`` in an unused vendor extension used to fail the whole build.
    Keys of name maps (a property or header called ``x-request-id``) are names, not
    extensions, and example data is left alone.
    """
    from nw_connector_builder.normalize_v2 import _DATA_KEYS, _NAME_MAP_KEYS

    if isinstance(node, dict):
        for key in list(node):
            # `x-nw-` keys are this generator's own markers (set by the 2.0 converter).
            if not _name_map and str(key).startswith("x-") and not str(key).startswith("x-nw-"):
                del node[key]
                continue
            # An empty YAML field (`parameters:`) is null, which no OpenAPI field allows
            # outside data; the validator crashes on it. Data nulls (`default: null`) stay.
            if not _name_map and node[key] is None and key not in _DATA_KEYS:
                del node[key]
                continue
            if not _name_map and key in _DATA_KEYS:
                continue
            drop_extensions(node[key], _name_map=not _name_map and key in _NAME_MAP_KEYS)
    elif isinstance(node, list):
        for item in node:
            drop_extensions(item)


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
        doc = _string_keys(yaml.load(text, Loader=_SpecLoader))  # nosec B506 - safe loader
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
    raise SpecLoadError("Unrecognized OpenAPI version (expected swagger: '2.0' or openapi: '3.x')")


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
    drop_extensions(doc)
    remotes = collect_remote_refs(doc, from_url=from_url)
    if remotes:
        listed = "\n".join(f"  - {r}" for r in remotes)
        raise SpecLoadError(f"Remote $ref values are not allowed:\n{listed}")

    try:
        from prance.util.resolver import RefResolver
    except ImportError as exc:
        raise SpecLoadError(
            "prance[osv] / openapi-spec-validator required (pip install 'prance[osv]')"
        ) from exc

    if not base_url:
        raise SpecLoadError(
            "Internal error: RefResolver requires a base_url for fragment $ref resolution"
        )

    cut_refs: set[str] = set()

    def _cut_recursive_ref(_limit: int, parsed_url: Any, _recursions: Any = ()) -> dict[str, Any]:
        # A $ref cycle (Graph's chatMessage.replies, team <-> channel) never finishes
        # inlining. Replace the edge that re-enters a schema with a permissive object.
        target = parsed_url.geturl()
        cut_refs.add(target.rsplit("/", 1)[-1])
        return {"type": "object", "additionalProperties": True, "x-recursive-ref": target}

    try:
        resolver = RefResolver(doc, url=base_url, recursion_limit_handler=_cut_recursive_ref)
        resolver.resolve_references()
        resolved = resolver.specs
        if not isinstance(resolved, dict):
            raise SpecLoadError("ref resolver returned a non-object specification")
        if cut_refs:
            logger.warning(
                "Cut recursive $ref cycles at %d schema(s), now permissive objects: %s",
                len(cut_refs),
                ", ".join(sorted(cut_refs)),
            )
        # After resolution so inlined $ref targets are covered too.
        repaired = sanitize_draft4_schemas(resolved)
        if repaired:
            logger.warning(
                "Repaired draft-4 schema constructs invalid in OpenAPI 3.0: %s",
                ", ".join(f"{k}={v}" for k, v in sorted(repaired.items())),
            )
        _validate_dropping_invalid_parts(resolved)
    except SpecLoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpecLoadError(f"OpenAPI validation/deref failed: {exc}") from exc

    return resolved


# Set on the resolved document for derive: operations and components that failed
# validation on their own and were removed, so the rest of the spec still builds.
INVALID_OPERATIONS_KEY = "x-nw-invalid-operations"
INVALID_COMPONENTS_KEY = "x-nw-invalid-components"
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")


class _ValidatorCrash:
    """Stands in for a validation error when the validator raises on a part."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.context: list[Any] = []
        self.absolute_path: list[Any] = []


def _leaf_reason(error: Any) -> str:
    """The most specific message under a validation error, kept short.

    ``oneOf`` failures repeat the whole offending object in their message; the deepest
    sub-error says what is actually wrong (``... has non-unique elements``).
    """
    leaves = [error]
    stack = list(getattr(error, "context", None) or [])
    while stack:
        sub = stack.pop()
        children = list(getattr(sub, "context", None) or [])
        if children:
            stack.extend(children)
        else:
            leaves.append(sub)
    best = max(leaves, key=lambda e: len(list(e.absolute_path)) - len(e.message) / 10_000)
    message = " ".join(str(best.message).split())
    return message if len(message) <= 160 else message[:157] + "..."


def _validate_dropping_invalid_parts(resolved: dict[str, Any]) -> None:
    """Validate ``resolved``; if it fails, drop just the operations and components that fail.

    Community specs are often valid except for one operation (Twitch's scraped spec lists a
    ``required`` entry twice in one response). Rejecting the whole document for that would
    leave nothing to build. Each operation and component is checked on its own (references
    are already inlined), the invalid ones are removed and recorded under
    :data:`INVALID_OPERATIONS_KEY` / :data:`INVALID_COMPONENTS_KEY`, and the rest is validated
    again; anything still invalid fails the build as before.
    """
    from openapi_spec_validator import validate
    from openapi_spec_validator.shortcuts import get_validator_cls

    # Any failure here is narrowed below, part by part.
    with contextlib.suppress(Exception):
        validate(resolved)
        return

    validator_cls = get_validator_cls(resolved)
    base = {k: resolved[k] for k in ("openapi", "info") if k in resolved}

    def _errors(probe: dict[str, Any]) -> list[Any]:
        try:
            return list(validator_cls({**base, **probe}).iter_errors())
        except Exception as exc:  # noqa: BLE001 - the validator itself failed on this part
            return [_ValidatorCrash(f"could not be validated ({type(exc).__name__}: {exc})")]

    invalid_ops: list[dict[str, Any]] = []
    for path, item in list((resolved.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        shared = {
            k: v for k, v in item.items() if k not in _HTTP_METHODS and k != "additionalOperations"
        }
        extra = item.get("additionalOperations")
        candidates = [(m, item, m) for m in _HTTP_METHODS] + (
            [(m, extra, m) for m in list(extra)] if isinstance(extra, dict) else []
        )
        for method, owner, key in candidates:
            op = owner.get(key)
            if not isinstance(op, dict):
                continue
            probe_item = (
                {**shared, method: op}
                if owner is item
                else {**shared, "additionalOperations": {key: op}}
            )
            errors = _errors({"paths": {path: probe_item}})
            if errors:
                del owner[key]
                invalid_ops.append(
                    {
                        "method": method,
                        "path": path,
                        "operationId": op.get("operationId"),
                        "reason": _leaf_reason(errors[0]),
                    }
                )

    invalid_components: list[str] = []
    for kind, entries in list((resolved.get("components") or {}).items()):
        if not isinstance(entries, dict):
            continue
        for name, obj in list(entries.items()):
            if _errors({"paths": {}, "components": {kind: {name: obj}}}):
                del entries[name]
                invalid_components.append(f"{kind}/{name}")

    if invalid_ops:
        resolved[INVALID_OPERATIONS_KEY] = invalid_ops
    if invalid_components:
        resolved[INVALID_COMPONENTS_KEY] = invalid_components
    validate(resolved)


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
    content_hash = hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()

    if version == "2.0":
        logger.info("Normalizing Swagger 2.0 → OpenAPI 3.0")
        doc = normalize_swagger2_to_openapi3(doc, source_url=origin if from_url else None)

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
    if from_url:
        _resolve_server_urls(resolved, origin)
    return resolved, meta


def _resolve_server_urls(doc: dict[str, Any], origin: str) -> None:
    """Server URLs relative to where the document was fetched from, as OpenAPI 3 says.

    "A relative server URL is relative to the location where the OpenAPI document is
    being served"; with no ``servers`` at all the default is ``/``.
    """
    from urllib.parse import urljoin

    servers = doc.get("servers")
    if not isinstance(servers, list) or not servers:
        doc["servers"] = [{"url": urljoin(origin, "/")}]
        return
    for server in servers:
        if isinstance(server, dict) and isinstance(server.get("url"), str):
            url = server["url"]
            if "://" not in url and "{" not in url.split("/", 1)[0]:
                server["url"] = urljoin(origin, url)

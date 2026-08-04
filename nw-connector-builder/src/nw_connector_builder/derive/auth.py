# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Connector-level auth collapse from OpenAPI security."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


AuthMode = Literal["required", "anonymous", "optional", "unsupported", "divergent", "and_multi"]


@dataclass
class ConnectorAuthPlan:
    """Chosen connector-level auth (+ report snippet)."""

    scheme_name: str | None
    scheme: dict[str, Any] | None
    provider: str | None  # static_token | apikey_query | none
    secret_key: str
    yaml_block: dict[str, Any]
    notes: list[str]


@dataclass
class OpSecurityDecision:
    mode: AuthMode
    reason: str | None = None


_UNSUPPORTED_TYPES = frozenset({"oauth2", "openIdConnect", "mutualTLS"})


def _scheme_supported(scheme: dict[str, Any] | None) -> bool:
    if not scheme:
        return False
    t = scheme.get("type")
    if t in _UNSUPPORTED_TYPES:
        return False
    if t == "apiKey":
        return scheme.get("in") in {"header", "query"}
    if t == "http":
        return str(scheme.get("scheme", "")).lower() in {"bearer", "basic"}
    return False


def _scheme_fingerprint(name: str, scheme: dict[str, Any]) -> str:
    t = scheme.get("type")
    if t == "apiKey":
        return f"apiKey:{scheme.get('in')}:{scheme.get('name')}"
    if t == "http":
        return f"http:{scheme.get('scheme')}"
    return f"{t}:{name}"


def build_auth_plan(
    connector_id: str,
    schemes: dict[str, Any],
    chosen_name: str | None,
) -> ConnectorAuthPlan:
    upper = connector_id.upper()
    notes: list[str] = []
    if not chosen_name or chosen_name not in schemes:
        return ConnectorAuthPlan(
            scheme_name=None,
            scheme=None,
            provider="none",
            secret_key="",
            yaml_block={},
            notes=["No connector-level auth scheme (anonymous / no supported schemes)"],
        )

    scheme = schemes[chosen_name]
    t = scheme.get("type")
    if t == "apiKey" and scheme.get("in") == "query":
        secret_key = f"{upper}_API_KEY"
        block = {
            "provider": "apikey_query",
            "name": scheme.get("name"),
            "secret_key": secret_key,
        }
        return ConnectorAuthPlan(
            scheme_name=chosen_name,
            scheme=scheme,
            provider="apikey_query",
            secret_key=secret_key,
            yaml_block=block,
            notes=notes,
        )

    if t == "apiKey" and scheme.get("in") == "header":
        secret_key = f"{upper}_API_KEY"
        block = {
            "provider": "static_token",
            "secret_key": secret_key,
            "header_name": scheme.get("name") or "X-API-Key",
            "prefix": "",
        }
        return ConnectorAuthPlan(
            scheme_name=chosen_name,
            scheme=scheme,
            provider="static_token",
            secret_key=secret_key,
            yaml_block=block,
            notes=notes,
        )

    if t == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
        secret_key = f"{upper}_TOKEN"
        block = {"provider": "static_token", "secret_key": secret_key}
        return ConnectorAuthPlan(
            scheme_name=chosen_name,
            scheme=scheme,
            provider="static_token",
            secret_key=secret_key,
            yaml_block=block,
            notes=notes,
        )

    if t == "http" and str(scheme.get("scheme", "")).lower() == "basic":
        secret_key = f"{upper}_BASIC_AUTH"
        block = {
            "provider": "static_token",
            "secret_key": secret_key,
            "prefix": "Basic",
            "encoding": "base64",
        }
        return ConnectorAuthPlan(
            scheme_name=chosen_name,
            scheme=scheme,
            provider="static_token",
            secret_key=secret_key,
            yaml_block=block,
            notes=notes,
        )

    return ConnectorAuthPlan(
        None, None, "none", "", {}, notes=["Chosen scheme could not be mapped"]
    )


def evaluate_operation_security(
    op_security: Any,
    doc_security: Any,
    schemes: dict[str, Any],
    connector_fp: str | None,
) -> OpSecurityDecision:
    """Resolve op-level security against the connector-level fingerprint."""
    if op_security is None:
        security = doc_security
    else:
        security = op_security

    if security == []:
        return OpSecurityDecision("anonymous")

    if security is None:
        # No security at all → treat as optional/default (send connector auth if any)
        return OpSecurityDecision("optional" if connector_fp else "anonymous")

    if not isinstance(security, list):
        return OpSecurityDecision("unsupported", "malformed security")

    # Empty object requirement = optional auth
    if len(security) == 1 and security[0] == {}:
        return OpSecurityDecision("optional")

    candidates: list[str] = []
    saw_unsupported_only = False
    for req in security:
        if not isinstance(req, dict):
            continue
        if req == {}:
            continue
        if len(req) > 1:
            return OpSecurityDecision(
                "and_multi", "AND multi-scheme security requirement not supported"
            )
        name = next(iter(req.keys()))
        scheme = schemes.get(name)
        if not _scheme_supported(scheme):
            saw_unsupported_only = True
            continue
        assert scheme is not None
        candidates.append(_scheme_fingerprint(name, scheme))

    if not candidates:
        if saw_unsupported_only:
            return OpSecurityDecision(
                "unsupported", "oauth2/openIdConnect/mutualTLS or unknown scheme"
            )
        return OpSecurityDecision("optional" if connector_fp else "anonymous")

    # OR of requirements — keep if any matches connector scheme
    if connector_fp is None:
        return OpSecurityDecision("optional")
    if connector_fp in candidates:
        return OpSecurityDecision("required")
    return OpSecurityDecision(
        "divergent", f"requires scheme other than connector-level {connector_fp}"
    )


def choose_connector_scheme(
    doc: dict[str, Any],
    schemes: dict[str, Any],
) -> str | None:
    """Pick global security scheme else most common required supported scheme."""
    doc_sec = doc.get("security")
    if isinstance(doc_sec, list):
        for req in doc_sec:
            if isinstance(req, dict) and len(req) == 1:
                name = next(iter(req.keys()))
                if _scheme_supported(schemes.get(name)):
                    return name

    counts: dict[str, int] = {}
    for path_item in (doc.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.startswith("x-") or method == "parameters" or not isinstance(op, dict):
                continue
            sec = op.get("security", doc_sec)
            if sec == [] or sec is None:
                continue
            if not isinstance(sec, list):
                continue
            for req in sec:
                if not isinstance(req, dict) or len(req) != 1:
                    continue
                name = next(iter(req.keys()))
                if _scheme_supported(schemes.get(name)):
                    counts[name] = counts.get(name, 0) + 1

    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def connector_fingerprint(name: str | None, schemes: dict[str, Any]) -> str | None:
    if not name or name not in schemes:
        return None
    return _scheme_fingerprint(name, schemes[name])

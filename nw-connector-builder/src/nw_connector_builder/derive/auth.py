# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Connector-level auth collapse from OpenAPI security."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AuthMode = Literal["required", "anonymous", "optional", "unsupported", "divergent", "and_multi"]

# OpenAPI oauth2 flow -> Node Wire OAuth2AuthProvider grant_method. Only flows that can
# run unattended (app-only client_credentials) or that reduce to a non-interactive grant
# after a one-time out-of-band step (authorizationCode -> refresh_token) are mapped.
# `implicit` and `password` are deliberately never supported — see nw-connector-builder-scope.md.
OAuth2FlowKind = Literal["client_credentials", "authorization_code"]


@dataclass
class ConnectorAuthPlan:
    """Chosen connector-level auth (+ report snippet)."""

    scheme_name: str | None
    scheme: dict[str, Any] | None
    provider: str | None  # static_token | apikey_query | oauth2 | none
    secret_key: str  # primary secret, for back-compat display (oauth2: the durable one)
    yaml_block: dict[str, Any]
    notes: list[str]
    secret_keys: list[str] = field(default_factory=list)
    secret_defaults: dict[str, str] = field(default_factory=dict)
    # "self_managed": Node Wire owns acquisition + presentation (or no auth).
    # "host_supplied": Node Wire only presents a bearer token the host obtains/rotates.
    tier: Literal["self_managed", "host_supplied"] = "self_managed"


@dataclass
class OpSecurityDecision:
    mode: AuthMode
    reason: str | None = None
    # For "divergent": the presentable scheme this op needs (codegen routes to it).
    # None for "required"/"optional" — those use the connector default.
    scheme_name: str | None = None


_UNSUPPORTED_TYPES = frozenset({"openIdConnect", "mutualTLS"})


def _oauth2_flow_kind(scheme: dict[str, Any] | None) -> OAuth2FlowKind | None:
    """Which (if any) supported grant this oauth2 scheme's declared flows map to.

    ``clientCredentials`` wins when both are declared (fully unattended beats a flow
    that needs a one-time manual step). ``implicit`` / ``password`` are never mapped.
    """
    if not scheme or scheme.get("type") != "oauth2":
        return None
    flows = scheme.get("flows")
    if not isinstance(flows, dict):
        return None
    if isinstance(flows.get("clientCredentials"), dict):
        return "client_credentials"
    if isinstance(flows.get("authorizationCode"), dict):
        return "authorization_code"
    return None


def _oauth2_flow_details(scheme: dict[str, Any], kind: OAuth2FlowKind) -> dict[str, Any]:
    """Pull ``tokenUrl`` / ``authorizationUrl`` / scope names out of the chosen flow."""
    flows = scheme.get("flows") or {}
    key = "clientCredentials" if kind == "client_credentials" else "authorizationCode"
    flow = flows.get(key) or {}
    return {
        "token_url": flow.get("tokenUrl"),
        "authorization_url": flow.get("authorizationUrl"),
        "scopes": list((flow.get("scopes") or {}).keys()),
    }


def _scheme_supported(scheme: dict[str, Any] | None) -> bool:
    if not scheme:
        return False
    t = scheme.get("type")
    if t == "oauth2":
        return _oauth2_flow_kind(scheme) is not None
    if t in _UNSUPPORTED_TYPES:
        return False
    if t == "apiKey":
        return scheme.get("in") in {"header", "query"}
    if t == "http":
        return str(scheme.get("scheme", "")).lower() in {"bearer", "basic"}
    return False


def _scheme_host_supplied(scheme: dict[str, Any] | None) -> bool:
    """Host-supplied schemes: presentable as a bearer token, never acquired by Node Wire.

    Covers oauth2 with no unattended grant (``implicit``, ``password``, etc.) and
    ``openIdConnect``. Contrast ``_scheme_supported``, which is for schemes Node Wire
    fully owns end to end.
    """
    if not scheme:
        return False
    t = scheme.get("type")
    if t == "oauth2":
        return _oauth2_flow_kind(scheme) is None
    return t == "openIdConnect"


def _scheme_presentable(scheme: dict[str, Any] | None) -> bool:
    """True for anything Node Wire can attach to an outbound request at all —
    ``_scheme_supported`` (self-managed) or ``_scheme_host_supplied``. False
    only for genuinely unpresentable schemes: mutualTLS (a transport-layer
    client certificate, not a header/param), apiKey-in-cookie (no
    ``name=value`` cookie formatting in ``StaticTokenAuthProvider`` yet), and
    unrecognized scheme types.
    """
    return _scheme_supported(scheme) or _scheme_host_supplied(scheme)


def _scheme_fingerprint(name: str, scheme: dict[str, Any]) -> str:
    t = scheme.get("type")
    if t == "apiKey":
        return f"apiKey:{scheme.get('in')}:{scheme.get('name')}"
    if t == "http":
        return f"http:{scheme.get('scheme')}"
    if t == "oauth2":
        return f"oauth2:{_oauth2_flow_kind(scheme)}:{name}"
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
            secret_keys=[secret_key],
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
            secret_keys=[secret_key],
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
            secret_keys=[secret_key],
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
            secret_keys=[secret_key],
        )

    if t == "oauth2":
        kind = _oauth2_flow_kind(scheme)
        if kind is None:
            return _build_host_supplied_auth_plan(upper, chosen_name, scheme)
        return _build_oauth2_auth_plan(upper, chosen_name, scheme, kind)

    if t == "openIdConnect":
        return _build_host_supplied_auth_plan(upper, chosen_name, scheme)

    return ConnectorAuthPlan(
        None, None, "none", "", {}, notes=["Chosen scheme could not be mapped"]
    )


def _build_host_supplied_auth_plan(
    upper: str,
    chosen_name: str,
    scheme: dict[str, Any],
) -> ConnectorAuthPlan:
    """Scaffold a host-supplied bearer for a scheme Node Wire never acquires.

    Presentation only — no acquisition, refresh, or expiry handling. The host
    must supply and rotate the token (see nw-connector-builder-scope.md).
    """
    t = scheme.get("type")
    if t == "oauth2":
        flows = sorted((scheme.get("flows") or {}).keys()) or ["<none declared>"]
        origin = f"declares oauth2 flow(s) {flows!r} with no unattended grant Node Wire can run"
    else:
        origin = "declares openIdConnect, whose underlying flow can't be introspected from the spec"

    secret_key = f"{upper}_ACCESS_TOKEN"
    block = {
        "provider": "static_token",
        "secret_key": secret_key,
        "header_name": "Authorization",
        "prefix": "Bearer",
        "host_supplied": True,
    }
    notes = [
        f"HOST-SUPPLIED CREDENTIAL: scheme {chosen_name!r} {origin}. Node Wire never "
        "acquires this credential — the host application must obtain it (e.g. complete "
        f"the provider's own auth flow) and set {secret_key} itself. Node Wire will "
        "present whatever value is there as a Bearer token but performs no refresh and "
        "detects no expiry; a stale token surfaces as a plain 401 from the API, not a "
        "managed refresh cycle."
    ]
    return ConnectorAuthPlan(
        scheme_name=chosen_name,
        scheme=scheme,
        provider="static_token",
        secret_key=secret_key,
        yaml_block=block,
        notes=notes,
        secret_keys=[secret_key],
        tier="host_supplied",
    )


def _build_oauth2_auth_plan(
    upper: str,
    chosen_name: str,
    scheme: dict[str, Any],
    kind: OAuth2FlowKind,
) -> ConnectorAuthPlan:
    """Scaffold an oauth2 connector-level auth plan for a supported flow.

    Neither flow is minted by Node Wire from spec data alone — see
    ``docs/nw-connector-builder-scope.md``. What *is* derivable from the spec
    (token endpoint, declared scopes) is pre-filled; secrets the host app must
    provision (client id/secret, and for authorization_code a refresh token
    obtained via a one-time interactive consent) are emitted as blank
    placeholders in ``sample.env``.
    """
    details = _oauth2_flow_details(scheme, kind)
    token_url_secret = f"{upper}_TOKEN_URL"
    client_id_secret = f"{upper}_CLIENT_ID"
    client_secret_secret = f"{upper}_CLIENT_SECRET"

    block: dict[str, Any] = {
        "provider": "oauth2",
        "token_url_secret": token_url_secret,
        "client_id_secret": client_id_secret,
        "client_secret_secret": client_secret_secret,
    }
    secret_keys = [token_url_secret, client_id_secret, client_secret_secret]
    secret_defaults: dict[str, str] = {}
    if details["token_url"]:
        # Not actually secret (it's public API metadata) but resolved the same way as
        # the rest of the block so a sandbox/prod override never needs a code change.
        secret_defaults[token_url_secret] = details["token_url"]

    scopes = list(details["scopes"])
    notes: list[str] = []
    if kind == "client_credentials":
        block["grant_method"] = "client_secret_post"
        primary_secret = client_secret_secret
        notes.append(
            "OAuth2 client_credentials (app-only, unattended): register an application "
            f"with the API provider, then set {client_id_secret} / {client_secret_secret}. "
            f"{token_url_secret} is pre-filled in sample.env from the spec."
        )
    else:
        refresh_token_secret = f"{upper}_REFRESH_TOKEN"
        block["grant_method"] = "refresh_token"
        block["refresh_token_secret"] = refresh_token_secret
        secret_keys.append(refresh_token_secret)
        primary_secret = refresh_token_secret
        where = f" at {details['authorization_url']}" if details["authorization_url"] else ""
        notes.append(
            "OAuth2 authorizationCode flow: Node Wire does not perform the interactive "
            f"consent{where} — complete it once, out-of-band, then set {refresh_token_secret} "
            f"(plus {client_id_secret} / {client_secret_secret}). Access tokens are refreshed "
            "automatically from it afterward. If the IdP rotates the refresh token, the "
            "connector will keep working in-process either way; register the host's own "
            "OAuth2AuthProvider(on_refresh_token_rotated=...) hook so the replacement also "
            "survives a restart."
        )
        if "offline_access" not in scopes:
            scopes.append("offline_access")
            notes.append(
                "Added 'offline_access' to scopes — the spec's declared scope list didn't "
                "include it, but most OIDC providers (Microsoft identity platform included) "
                "will not issue a refresh token during the interactive consent without it. "
                "Request this same scope list during that consent step. (Some providers, e.g. "
                "Google, use a request parameter instead of a scope for offline access — check "
                "your provider's docs if this API isn't one of the common ones.)"
            )

    if scopes:
        block["scopes"] = scopes

    return ConnectorAuthPlan(
        scheme_name=chosen_name,
        scheme=scheme,
        provider="oauth2",
        secret_key=primary_secret,
        yaml_block=block,
        notes=notes,
        secret_keys=secret_keys,
        secret_defaults=secret_defaults,
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

    candidates: list[tuple[str, str]] = []  # (scheme_name, fingerprint)
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
        # Presentable = self-managed or host-supplied; fingerprint mismatch -> divergent.
        if not _scheme_presentable(scheme):
            saw_unsupported_only = True
            continue
        assert scheme is not None
        candidates.append((name, _scheme_fingerprint(name, scheme)))

    if not candidates:
        if saw_unsupported_only:
            return OpSecurityDecision(
                "unsupported", "mutualTLS, apiKey-in-cookie, or unknown scheme"
            )
        return OpSecurityDecision("optional" if connector_fp else "anonymous")

    # OR of requirements — keep if any matches connector scheme
    if connector_fp is None:
        return OpSecurityDecision("optional")
    if connector_fp in (fp for _, fp in candidates):
        return OpSecurityDecision("required")
    # Presentable but not the connector default — route by name instead of dropping.
    divergent_name = candidates[0][0]
    return OpSecurityDecision(
        "divergent",
        f"uses scheme {divergent_name!r}, connector default is a different scheme",
        scheme_name=divergent_name,
    )


def _pick_named_global_scheme(doc_sec: Any, schemes: dict[str, Any], predicate: Any) -> str | None:
    if not isinstance(doc_sec, list):
        return None
    for req in doc_sec:
        if isinstance(req, dict) and len(req) == 1:
            name = next(iter(req.keys()))
            if predicate(schemes.get(name)):
                return name
    return None


def _count_scheme_usage(
    doc: dict[str, Any], schemes: dict[str, Any], doc_sec: Any, predicate: Any
) -> dict[str, int]:
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
                if predicate(schemes.get(name)):
                    counts[name] = counts.get(name, 0) + 1
    return counts


def choose_connector_scheme(
    doc: dict[str, Any],
    schemes: dict[str, Any],
) -> str | None:
    """Pick global security scheme else most common required supported scheme.

    Self-managed schemes (``_scheme_supported``) always win when present — so a
    mixed-scheme Petstore-shaped doc keeps ``api_key`` as default. Host-supplied
    schemes are only considered when no self-managed scheme exists, so they cannot
    outvote a mapped scheme by raw usage count.
    """
    doc_sec = doc.get("security")

    picked = _pick_named_global_scheme(doc_sec, schemes, _scheme_supported)
    if picked is not None:
        return picked

    counts = _count_scheme_usage(doc, schemes, doc_sec, _scheme_supported)
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]

    picked = _pick_named_global_scheme(doc_sec, schemes, _scheme_host_supplied)
    if picked is not None:
        return picked

    host_counts = _count_scheme_usage(doc, schemes, doc_sec, _scheme_host_supplied)
    if not host_counts:
        return None
    return max(host_counts.items(), key=lambda kv: kv[1])[0]


def connector_fingerprint(name: str | None, schemes: dict[str, Any]) -> str | None:
    if not name or name not in schemes:
        return None
    return _scheme_fingerprint(name, schemes[name])

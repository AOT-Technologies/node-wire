# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Connector-level auth collapse from OpenAPI security."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, NamedTuple


AuthMode = Literal["required", "anonymous", "optional", "unsupported", "divergent", "and_multi"]

# Flow kind is retained only for operation fingerprints so per-operation matching
# stays stable across regenerations. Generated connectors never run these grants —
# every oauth2 scheme is host-supplied (see nw-connector-builder-scope.md).
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


# Presentable as a bearer, but never acquired by Node Wire — see _scheme_host_supplied.
_HOST_SUPPLIED_TYPES = frozenset({"oauth2", "openIdConnect"})
# Not presentable at all (transport-layer credential): operations are soft-dropped.
_UNSUPPORTED_TYPES = frozenset({"mutualTLS"})


def _oauth2_flow_kind(scheme: dict[str, Any] | None) -> OAuth2FlowKind | None:
    """Which declared oauth2 flow (if any) this scheme names, for fingerprints only.

    ``clientCredentials`` wins when both are declared. ``implicit`` / ``password``
    return ``None``. Generated connectors never acquire tokens from these flows.
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


class _OAuth2Endpoints(NamedTuple):
    """Documentation-only URLs lifted from a scheme's declared oauth2 flows.

    Node Wire never calls either one — they go into the build report so the host
    knows where to obtain the token it must supply.
    """

    authorization_url: str | None
    token_url: str | None


_NO_OAUTH2_ENDPOINTS = _OAuth2Endpoints(None, None)


def _oauth2_endpoint_urls(scheme: dict[str, Any]) -> _OAuth2Endpoints:
    """Pull authorizationUrl / tokenUrl from any declared oauth2 flow for host docs."""
    flows = scheme.get("flows") or {}
    if not isinstance(flows, dict):
        return _NO_OAUTH2_ENDPOINTS
    auth_url: str | None = None
    grant_url: str | None = None
    for flow in flows.values():
        if not isinstance(flow, dict):
            continue
        if auth_url is None and flow.get("authorizationUrl"):
            auth_url = flow["authorizationUrl"]
        if grant_url is None and flow.get("tokenUrl"):
            grant_url = flow["tokenUrl"]
    return _OAuth2Endpoints(auth_url, grant_url)


def _scheme_supported(scheme: dict[str, Any] | None) -> bool:
    """Self-managed schemes Node Wire presents without host OAuth acquisition."""
    if not scheme:
        return False
    t = scheme.get("type")
    if t in _HOST_SUPPLIED_TYPES or t in _UNSUPPORTED_TYPES:
        return False
    if t == "apiKey":
        return scheme.get("in") in {"header", "query"}
    if t == "http":
        return str(scheme.get("scheme", "")).lower() in {"bearer", "basic"}
    return False


def _scheme_host_supplied(scheme: dict[str, Any] | None) -> bool:
    """Host-supplied schemes: presentable as a bearer token, never acquired by Node Wire.

    Covers every ``oauth2`` flow and ``openIdConnect``. Contrast ``_scheme_supported``,
    which is for schemes Node Wire fully owns end to end (apiKey / http bearer / basic).
    """
    if not scheme:
        return False
    return scheme.get("type") in _HOST_SUPPLIED_TYPES


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
            secret_key="",  # nosec B106  # env-var key name, empty means "no auth configured"
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

    if t in _HOST_SUPPLIED_TYPES:
        return _build_host_supplied_auth_plan(upper, chosen_name, scheme)

    return ConnectorAuthPlan(
        None, None, "none", "", {}, notes=["Chosen scheme could not be mapped"]
    )


def _scheme_qualified_secret_key(upper: str, scheme_name: str, secret_key: str) -> str:
    """Qualify a colliding secret with its scheme: ``<ID>_<SCHEME>_<SUFFIX>``.

    ``<SUFFIX>`` is what the un-qualified key already carries (``ACCESS_TOKEN``,
    ``API_KEY``, ``TOKEN``, ``BASIC_AUTH``), so the re-keyed name still reads as
    the same kind of credential.
    """
    scheme_upper = "".join(c if c.isalnum() else "_" for c in scheme_name).upper()
    prefix = f"{upper}_"
    suffix = secret_key[len(prefix) :] if secret_key.startswith(prefix) else secret_key
    return f"{upper}_{scheme_upper}_{suffix}"


def _rekey_plan(plan: ConnectorAuthPlan, old_key: str, new_key: str) -> ConnectorAuthPlan:
    """Return ``plan`` with ``old_key`` renamed to ``new_key`` everywhere it appears."""
    yaml_block = dict(plan.yaml_block)
    if yaml_block.get("secret_key") == old_key:
        yaml_block["secret_key"] = new_key
    return replace(
        plan,
        secret_key=new_key if plan.secret_key == old_key else plan.secret_key,
        yaml_block=yaml_block,
        secret_keys=[new_key if k == old_key else k for k in plan.secret_keys],
        secret_defaults={
            (new_key if k == old_key else k): v for k, v in plan.secret_defaults.items()
        },
        notes=[n.replace(old_key, new_key) for n in plan.notes],
    )


def uniquify_extra_secret_keys(
    connector_id: str,
    default: ConnectorAuthPlan,
    extras: dict[str, ConnectorAuthPlan],
) -> dict[str, ConnectorAuthPlan]:
    """Re-key extra schemes whose secret name is already taken by another scheme.

    Secret names are derived from the connector id and the credential kind, not the
    scheme name, so two schemes of the same kind (two ``apiKey`` headers, two
    ``oauth2`` flows) would otherwise share one secret despite needing different
    values. Collisions are qualified with the scheme name; the first claimant keeps
    the short name. Returns a new dict.
    """
    used = set(default.secret_keys)
    upper = connector_id.upper()
    out: dict[str, ConnectorAuthPlan] = {}
    for name, plan in extras.items():
        for old_key in list(plan.secret_keys):
            if old_key not in used:
                continue
            new_key = _scheme_qualified_secret_key(upper, name, old_key)
            attempt = 2
            while new_key in used:
                new_key = f"{_scheme_qualified_secret_key(upper, name, old_key)}_{attempt}"
                attempt += 1
            plan = _rekey_plan(plan, old_key, new_key)
        used.update(plan.secret_keys)
        out[name] = plan
    return out


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
    endpoints = _NO_OAUTH2_ENDPOINTS
    if t == "oauth2":
        flows = sorted((scheme.get("flows") or {}).keys()) or ["<none declared>"]
        origin = f"declares oauth2 flow(s) {flows!r}"
        endpoints = _oauth2_endpoint_urls(scheme)
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
    if endpoints.authorization_url:
        notes.append(f"Host auth documentation: authorizationUrl={endpoints.authorization_url}")
    if endpoints.token_url:
        notes.append(f"Host auth documentation: tokenUrl={endpoints.token_url}")
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

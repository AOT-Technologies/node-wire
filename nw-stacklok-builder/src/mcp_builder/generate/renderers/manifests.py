"""Render ToolHive deployment manifests from a ServerPlan.

Pipeline stage: rendering (plan -> YAML strings).
Produces Kubernetes-style manifests for deploying a generated MCP server
on ToolHive:

    - MCPServer CRD — always generated
    - MCPExternalAuthConfig CRD — only when auth is configured
    - MCPOIDCConfig CRD — only for embedded OAuth/OIDC auth
    - Secret template — only when auth type is api_key (bearerToken)
    - deploy/README.md — always generated (orientation + placeholders)

External access (Ingress / Gateway / LoadBalancer) is intentionally NOT
emitted: the URL shape is cluster-specific and generating a file that
looks authoritative but doesn't actually work is worse than generating
nothing. The README explains this to the user.

Each render_* function returns a string. ``render_manifests()`` is the
entry point — it returns a dict mapping filenames to content and handles
the conditional logic for auth-dependent manifests. Templates live in
``renderers/templates/*.jinja2``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from mcp_builder.generate.plan import ServerPlan
from mcp_builder.schema.models import OAuth2Auth, OIDCAuth

logger = logging.getLogger(__name__)

TOOLHIVE_API_VERSION = "toolhive.stacklok.dev/v1alpha1"
DEFAULT_NAMESPACE = "toolhive-system"

# Auth variants that use the embedded OIDC auth server (MCPOIDCConfig +
# MCPExternalAuthConfig). Use with ``isinstance`` so the type checker keeps
# the narrowed variant inside each branch.
_EMBEDDED_AUTH_CLASSES: tuple[type, ...] = (OAuth2Auth, OIDCAuth)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(  # nosec B701 — generating YAML manifests, not HTML
    loader=FileSystemLoader(_TEMPLATES_DIR),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_manifests(plan: ServerPlan) -> dict[str, str]:
    """Render all deployment manifests as a filename -> content mapping.

    Pipeline stage: rendering (plan -> {filename: YAML string}).

    Returns a dict with 2-4 entries depending on auth type:
        - "mcpserver.yaml" — always present
        - "README.md" — always present
        - "mcpexternalauthconfig.yaml" — present when auth.type != "none"
        - "mcpoidcconfig.yaml" — present when auth is oauth2 or oidc
        - "secret.yaml" — present only when auth.type == "api_key"
    """
    logger.info("Rendering deployment manifests for '%s'", plan.server_name)

    manifests: dict[str, str] = {
        "mcpserver.yaml": render_mcpserver(plan),
        "README.md": render_deploy_readme(plan),
    }

    if plan.auth.type != "none":
        manifests["mcpexternalauthconfig.yaml"] = render_external_auth_config(plan)

    if isinstance(plan.auth, _EMBEDDED_AUTH_CLASSES):
        manifests["mcpoidcconfig.yaml"] = render_mcpoidc_config(plan)

    if plan.auth.type == "api_key":
        manifests["secret.yaml"] = render_secret(plan)

    logger.info("Generated %d manifest(s)", len(manifests))
    return manifests


# ---------------------------------------------------------------------------
# Individual manifest renderers
# ---------------------------------------------------------------------------


def render_mcpserver(plan: ServerPlan) -> str:
    """Render the MCPServer CRD manifest."""
    logger.debug(
        "Rendering MCPServer for '%s' (auth=%s)", plan.server_name, plan.auth.type
    )
    tmpl = _env.get_template("mcpserver.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        api_version=TOOLHIVE_API_VERSION,
        namespace=DEFAULT_NAMESPACE,
        has_auth=plan.auth.type != "none",
        is_embedded_auth=isinstance(plan.auth, _EMBEDDED_AUTH_CLASSES),
    )


def render_mcpoidc_config(plan: ServerPlan) -> str:
    """Render the MCPOIDCConfig CRD manifest.

    Emitted for oauth2 and oidc auth — both use the embedded OIDC auth
    server, which issues its own OIDC tokens to clients regardless of
    whether the upstream is OAuth2 or OIDC. MCPServer references this
    resource via spec.oidcConfigRef instead of carrying an inline block.

    Raises ValueError for api_key / none auth.
    """
    if not isinstance(plan.auth, _EMBEDDED_AUTH_CLASSES):
        raise ValueError(
            f"MCPOIDCConfig only applies to oauth2/oidc auth, got {plan.auth.type!r}"
        )
    logger.debug("Rendering MCPOIDCConfig for '%s'", plan.server_name)
    tmpl = _env.get_template("mcpoidcconfig.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        api_version=TOOLHIVE_API_VERSION,
        namespace=DEFAULT_NAMESPACE,
    )


def render_external_auth_config(plan: ServerPlan) -> str:
    """Render the MCPExternalAuthConfig CRD manifest.

    Routes by auth variant:
        - OAuth2Auth -> embeddedAuthServer with an oauth2 upstream provider
          (authorizationEndpoint / tokenEndpoint declared inline)
        - OIDCAuth -> embeddedAuthServer with an oidc upstream provider
          (upstream publishes its own discovery document)
        - APIKeyAuth -> bearerToken with a tokenSecretRef

    Raises ValueError for auth.type == "none".
    """
    if isinstance(plan.auth, OAuth2Auth):
        logger.debug("Rendering embedded OAuth2 auth config for '%s'", plan.server_name)
        return _render_embedded_oauth2(plan, plan.auth)

    if isinstance(plan.auth, OIDCAuth):
        logger.debug("Rendering embedded OIDC auth config for '%s'", plan.server_name)
        return _render_embedded_oidc(plan, plan.auth)

    if plan.auth.type == "api_key":
        logger.debug("Rendering bearer token auth config for '%s'", plan.server_name)
        return _render_bearer_token_auth(plan)

    raise ValueError(f"No external auth config for auth type {plan.auth.type!r}")


def render_secret(plan: ServerPlan) -> str:
    """Render the K8s Secret template for bearerToken auth.

    Only generated for api_key auth. OAuth auth does not need a
    user-provided secret — signing keys are auto-generated by ToolHive
    at runtime.

    Raises ValueError if called with a non-api_key auth type.
    """
    if plan.auth.type != "api_key":
        raise ValueError(
            f"Secret template is only for api_key auth, got {plan.auth.type!r}"
        )

    logger.debug("Rendering Secret template for '%s'", plan.server_name)
    tmpl = _env.get_template("secret.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        namespace=DEFAULT_NAMESPACE,
    )


def render_deploy_readme(plan: ServerPlan) -> str:
    """Render the deploy/README.md orientation doc.

    Explains the emitted files, placeholders, and — crucially — that the
    user must supply their own Ingress/Gateway/LoadBalancer. The shape of
    the external URL feeds back into the manifests (audience, issuer,
    redirectUri) so it can't be picked mechanically here.
    """
    logger.debug("Rendering deploy README for '%s'", plan.server_name)
    tmpl = _env.get_template("deploy_readme.md.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        has_auth=plan.auth.type != "none",
        is_embedded_auth=isinstance(plan.auth, _EMBEDDED_AUTH_CLASSES),
        is_api_key=plan.auth.type == "api_key",
    )


# ---------------------------------------------------------------------------
# Auth-type-specific renderers
# ---------------------------------------------------------------------------


def _render_embedded_oidc(plan: ServerPlan, auth: OIDCAuth) -> str:
    """Render MCPExternalAuthConfig for OIDC upstream (issuer-based).

    Emits ``upstreamProviders[*].type: oidc`` with ``oidcConfig.issuerUrl``.
    Endpoint URLs come from the upstream's discovery document at runtime.
    """
    provider_name = _derive_provider_name(auth.issuer)
    # openid+email are always present for user identity.
    required_scopes = ["openid", "email"]
    scopes = required_scopes + [
        s for s in auth.scopes_required if s not in required_scopes
    ]

    tmpl = _env.get_template("authconfig_embedded_oidc.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        api_version=TOOLHIVE_API_VERSION,
        namespace=DEFAULT_NAMESPACE,
        provider_name=provider_name,
        issuer_url=auth.issuer,
        scopes=scopes,
    )


def _render_embedded_oauth2(plan: ServerPlan, auth: OAuth2Auth) -> str:
    """Render MCPExternalAuthConfig for OAuth2 upstream (endpoint-based).

    Emits ``upstreamProviders[*].type: oauth2`` with ``oauth2Config``
    carrying inline ``authorizationEndpoint``, ``tokenEndpoint``, and an
    optional ``userInfo`` endpoint. No discovery document is consulted.
    """
    # Provider name derived from the authorization URL's host — OAuth2 has
    # no issuer to key off of.
    provider_name = _derive_provider_name(auth.authorization_url)
    scopes = list(auth.scopes_required)

    tmpl = _env.get_template("authconfig_embedded_oauth2.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        api_version=TOOLHIVE_API_VERSION,
        namespace=DEFAULT_NAMESPACE,
        provider_name=provider_name,
        authorization_url=auth.authorization_url,
        token_url=auth.token_url,
        userinfo_url=auth.userinfo_url,
        scopes=scopes,
    )


def _render_bearer_token_auth(plan: ServerPlan) -> str:
    """Render MCPExternalAuthConfig for API key (bearerToken type)."""
    tmpl = _env.get_template("authconfig_bearer.yaml.jinja2")
    return tmpl.render(
        server_name=plan.server_name,
        api_version=TOOLHIVE_API_VERSION,
        namespace=DEFAULT_NAMESPACE,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_provider_name(issuer: str) -> str:
    """Derive a short provider name from an OAuth issuer URL.

    Extracts the domain and picks a recognizable short name when possible.
    Falls back to the second-level domain label.

    Examples:
        "https://accounts.google.com" -> "google"
        "https://login.microsoftonline.com/..." -> "microsoft"
        "https://auth.atlassian.com/..." -> "atlassian"
        "https://my-company.okta.com" -> "okta"
        "https://github.com/login/oauth" -> "github"
    """
    try:
        hostname = urlparse(issuer).hostname or issuer
    except Exception:
        hostname = issuer

    # Known provider patterns
    known: list[tuple[str, str]] = [
        ("google", "google"),
        ("microsoft", "microsoft"),
        ("okta", "okta"),
        ("auth0", "auth0"),
        ("atlassian", "atlassian"),
        ("github", "github"),
        ("gitlab", "gitlab"),
        ("slack", "slack"),
        ("amazon", "amazon"),
        ("apple", "apple"),
    ]
    hostname_lower = hostname.lower()
    for pattern, name in known:
        if pattern in hostname_lower:
            logger.debug("Matched known provider '%s' from issuer '%s'", name, issuer)
            return name

    # Fall back to second-level domain (e.g., "example" from "sso.example.com")
    parts = hostname_lower.split(".")
    if len(parts) >= 2:
        fallback = re.sub(r"[^a-z0-9-]", "", parts[-2])
        logger.info(
            "No known provider matched for issuer '%s'; using domain label '%s'",
            issuer,
            fallback,
        )
        return fallback

    logger.warning(
        "Could not derive provider name from issuer '%s'; using 'upstream'", issuer
    )
    return "upstream"

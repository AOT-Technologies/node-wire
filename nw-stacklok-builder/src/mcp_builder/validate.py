"""Scope validation: check an MCPScope against an OpenAPI spec.

Pipeline stage: validation (scope + optional spec → errors/warnings).
This module sits at the same abstraction level as plan.py and analyzer.py —
it's a domain operation that consumes the low-level spec/ and schema/ packages.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field

from mcp_builder.spec.media import is_json_media_type, is_text_media_type
from mcp_builder.schema.models import (
    AuthConfig,
    MCPScope,
    OAuth2Auth,
    OIDCAuth,
    ParamLocation,
)
from mcp_builder.spec import (
    ExtractedResponse,
    OpenAPISpec,
    get_body_fields,
    get_parameters,
    get_response_content_types,
    parse_endpoint,
)

# Matches one ``{name}`` placeholder. The inner grammar matches what the
# scoping stage emits for tenant substitution — identifier-shaped tokens
# plus hyphens and dots (e.g. ``{company-domain}``, ``{tenant.region}``).
# The leading-char restriction keeps regex literals (``{0,5}``) and YAML
# brace usage from masquerading as unresolved placeholders.
_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.\-]*\}")

logger = structlog.get_logger()


class ValidationResult(BaseModel):
    """Result of validating a scope, optionally against a spec."""

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_scope(
    scope: MCPScope, spec: OpenAPISpec | None = None
) -> ValidationResult:
    """Validate a scope file, optionally checking endpoints against a spec.

    Without a spec, performs structural checks on the scope itself.
    With a spec, also verifies that every endpoint in the scope exists
    in the spec.

    Args:
        scope: Validated MCPScope from a scope YAML file.
        spec: Optional typed OpenAPI spec to cross-reference.

    Returns:
        A ValidationResult with any errors and warnings found.
    """
    logger.info("validating scope", server_name=scope.server.name)
    errors: list[str] = []
    warnings: list[str] = []

    _check_unresolved_placeholders(scope, errors)
    _check_auth_consistency(scope, errors)

    # Check that groups have descriptions
    for group in scope.groups:
        if not group.description.strip():
            warnings.append(f"Group '{group.name}' has an empty description.")

    # Check tool hints and parameter overrides
    for group in scope.groups:
        for tool in group.tools:
            if tool.parameters:
                for param in tool.parameters:
                    if not param.description.strip():
                        warnings.append(
                            f"Parameter override '{param.name}' in tool "
                            f"'{tool.tool_name}' has an empty description."
                        )

    # Cross-reference with spec if provided
    if spec is not None:
        spec_paths = spec.paths or {}
        for group in scope.groups:
            for tool in group.tools:
                method, path = parse_endpoint(tool.endpoint)
                path_item = spec_paths.get(path)
                if path_item is None:
                    errors.append(
                        f"Tool '{tool.tool_name}': path '{path}' "
                        "not found in OpenAPI spec."
                    )
                    continue
                operation = getattr(path_item, method.lower(), None)
                if operation is None:
                    errors.append(
                        f"Tool '{tool.tool_name}': method '{method}' "
                        f"not found for path '{path}' in OpenAPI spec."
                    )
                    continue

                # Check that each YAML parameter exists in the spec.
                # Params missing from the spec will default to type str
                # during code generation, which may be wrong.
                spec_params = get_parameters(spec, method, path)
                spec_body = get_body_fields(spec, method, path)
                spec_param_names = {p.name for p in spec_params}
                spec_body_names = {f.name for f in spec_body}
                for param in tool.parameters:
                    if param.location == ParamLocation.BODY:
                        if param.name not in spec_body_names:
                            if spec_body_names:
                                # Spec declares body properties but the YAML
                                # param doesn't match any of them — typically
                                # a YAML authoring issue (e.g. a `body`
                                # catch-all), not an incomplete spec.
                                available = ", ".join(sorted(spec_body_names))
                                warnings.append(
                                    f"Tool '{tool.tool_name}': body parameter "
                                    f"'{param.name}' not found in spec's "
                                    f"requestBody properties "
                                    f"(available: {available}). Type will "
                                    f"default to str. Consider expanding "
                                    f"this into per-field body parameters "
                                    f"to match the spec schema."
                                )
                            else:
                                warnings.append(
                                    f"Tool '{tool.tool_name}': body parameter "
                                    f"'{param.name}' not found in spec's "
                                    f"requestBody — type will default to str. "
                                    f"The spec may be incomplete."
                                )
                    else:
                        if param.name not in spec_param_names:
                            warnings.append(
                                f"Tool '{tool.tool_name}': {param.location} "
                                f"parameter '{param.name}' not found in "
                                f"spec's parameters — type will default to "
                                f"str. The spec may be incomplete."
                            )

                # Scope's response_kind commits the generated tool to one
                # decode path; cross-check it against the spec so a
                # mismatch surfaces now instead of at runtime.
                responses = get_response_content_types(spec, method, path)
                _check_response_kind_matches_spec(
                    tool.tool_name,
                    tool.response_kind,
                    responses,
                    errors,
                    warnings,
                )

    logger.info(
        "validation complete",
        error_count=len(errors),
        warning_count=len(warnings),
    )
    return ValidationResult(errors=errors, warnings=warnings)


def _check_response_kind_matches_spec(
    tool_name: str,
    response_kind: Literal["json", "text", "binary", "auto"],
    responses: list[ExtractedResponse],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Cross-check a tool's scope ``response_kind`` against the spec's 2xx media types.

    The scope commits the generated tool to a single decode path. When
    the spec and the scope disagree, the fix is in the scope — the
    author controls the scope YAML; the upstream OpenAPI spec they
    typically don't.

    The generated client sends a different ``Accept`` header per kind:
    ``application/json`` for ``json``, ``text/*, */*;q=0.8`` for
    ``text``, and ``*/*`` for ``binary`` and ``auto``. A status that
    offers JSON plus text is therefore safe for either ``json`` or
    ``text`` — the author picks based on what the tool should return —
    but unsafe for ``binary``, which could hand the caller
    base64-wrapped JSON. ``auto`` opts out of this cross-check entirely:
    the author has signed up for runtime ``Content-Type`` dispatch
    precisely because no single decode path is correct for the operation.

    Outcomes:

    - ``response_kind="auto"``: silent pass (runtime dispatch is the
      author's explicit choice; spec shape is irrelevant).
    - Spec declares no 2xx responses: warning (can't verify the choice).
    - Spec's 2xx responses all have empty content (204-style): silent pass.
    - ``response_kind="json"`` but some 2xx status offers no JSON option: error.
    - ``response_kind="text"`` but some 2xx status offers no text option: error.
    - ``response_kind="binary"`` but some 2xx status offers JSON: error.
    - ``response_kind="binary"`` but every 2xx status is text-only: error.
    """
    if response_kind == "auto":
        return

    if not responses:
        warnings.append(
            f"Tool '{tool_name}': spec declares no 2xx responses — "
            "unable to verify the generated client can decode the "
            "response body. The spec may be incomplete."
        )
        return

    responses_with_content = [r for r in responses if r.media_types]
    if not responses_with_content:
        return  # All 2xx responses are 204-style; no body to decode.

    if response_kind == "json":
        # Every 2xx with content must offer at least one JSON media type
        # so the generated client's ``Accept: application/json`` header
        # can negotiate a JSON body. A status that offers both JSON and
        # XML is fine — the Accept header picks JSON.
        bad = [
            r
            for r in responses_with_content
            if not any(is_json_media_type(mt) for mt in r.media_types)
        ]
        if bad:
            detail = "; ".join(
                f"{resp.status_code} returns {', '.join(resp.media_types)}"
                for resp in sorted(bad, key=lambda r: r.status_code)
            )
            errors.append(
                f"Tool '{tool_name}': scope declares response_kind='json' "
                f"but spec's 2xx responses have no JSON content type "
                f"({detail}). Update the scope's response_kind to match "
                "the spec's media types (see the generator contract's "
                "'Picking a kind from the spec' section), or drop the "
                "endpoint from the scope."
            )
        return

    if response_kind == "text":
        # Every 2xx with content must offer at least one text/* (or XML)
        # media type so ``response.text`` returns a meaningful decode.
        # A status that offers text plus JSON is fine — the author
        # picked text intentionally over the json kind.
        bad = [
            r
            for r in responses_with_content
            if not any(is_text_media_type(mt) for mt in r.media_types)
        ]
        if bad:
            detail = "; ".join(
                f"{resp.status_code} returns {', '.join(resp.media_types)}"
                for resp in sorted(bad, key=lambda r: r.status_code)
            )
            errors.append(
                f"Tool '{tool_name}': scope declares response_kind='text' "
                f"but spec's 2xx responses have no text/* (or XML) content "
                f"type ({detail}). Update the scope's response_kind to "
                "match the spec's media types (see the generator "
                "contract's 'Picking a kind from the spec' section), or "
                "drop the endpoint from the scope."
            )
        return

    if response_kind == "binary":
        # No 2xx status may offer JSON — the generated client sends
        # ``Accept: */*``, so a JSON-capable server could return JSON
        # that would then get base64-wrapped and returned as opaque
        # bytes. Text/* media types are also generator-supported via
        # ``response_kind: text`` and should not be forced through the
        # base64 path, which hides human-readable content from the
        # model.
        json_bad = [
            r
            for r in responses_with_content
            if any(is_json_media_type(mt) for mt in r.media_types)
        ]
        if json_bad:
            detail = "; ".join(
                f"{resp.status_code} returns {', '.join(resp.media_types)}"
                for resp in sorted(json_bad, key=lambda r: r.status_code)
            )
            errors.append(
                f"Tool '{tool_name}': scope declares response_kind='binary' "
                f"but spec's 2xx responses include JSON ({detail}). Update "
                "the scope to response_kind='json' or drop the endpoint "
                "from the scope."
            )
            return
        text_only = all(
            all(is_text_media_type(mt) for mt in r.media_types)
            for r in responses_with_content
        )
        if text_only:
            detail = "; ".join(
                f"{resp.status_code} returns {', '.join(resp.media_types)}"
                for resp in sorted(responses_with_content, key=lambda r: r.status_code)
            )
            errors.append(
                f"Tool '{tool_name}': scope declares response_kind='binary' "
                f"but spec's 2xx responses are all text ({detail}). "
                "response_kind='binary' base64-wraps readable text and "
                "hides it from the model. Update the scope to "
                "response_kind='text'."
            )
        return


def _auth_url_fields(auth: AuthConfig) -> list[tuple[str, str | None]]:
    """Return (name, value) pairs for every dialable URL on the auth block.

    Shared between V-TENANT-01 and V-AUTH-02 so adding a new URL field to
    ``OAuth2Auth`` or ``OIDCAuth`` (e.g. ``revocation_url``) updates both
    checks at once rather than silently dropping one.

    ``auth.issuer`` is intentionally excluded: it's an OIDC identifier
    consumed by deploy-assist when it fetches the discovery document,
    not a URL the generated server dials at request time. See
    ``_check_unresolved_placeholders`` for why placeholders in the
    issuer are handled differently.
    """
    if isinstance(auth, OAuth2Auth):
        return [
            ("auth.authorization_url", auth.authorization_url),
            ("auth.token_url", auth.token_url),
            ("auth.userinfo_url", auth.userinfo_url),
        ]
    if isinstance(auth, OIDCAuth):
        return [("auth.issuer", auth.issuer)]
    return []


def _check_unresolved_placeholders(scope: MCPScope, errors: list[str]) -> None:
    """Fail if any dialed URL still carries a ``{name}`` placeholder.

    Tenant-specific values (subdomain, region) belong in the scoping
    output, not in fields the deployed server will hit at request time.
    A placeholder reaching validate means scoping skipped substitution
    and the generated client will SSL-error on the literal hostname.

    ``auth.issuer`` is intentionally *not* scanned. For OIDC, the issuer
    is an identifier consumed by deploy-assist when it fetches the
    discovery document, and multi-tenant IdPs legitimately ship with
    placeholders in the issuer (Azure Entra v2's canonical issuer is
    ``https://login.microsoftonline.com/{tenantId}/v2.0``). Substitution
    for the issuer is deferred to deploy-assist, where the tenant
    context exists.

    Free-form fields (``notes``, descriptions) are also not scanned:
    prose can legitimately quote a placeholder for the reader.
    """
    fields: list[tuple[str, str | None]] = [("spec.base_url", scope.spec.base_url)]
    auth = scope.auth
    if isinstance(auth, OAuth2Auth):
        fields.append(("auth.authorization_url", auth.authorization_url))
        fields.append(("auth.token_url", auth.token_url))
        fields.append(("auth.userinfo_url", auth.userinfo_url))

    for field_name, value in fields:
        if value is None:
            continue
        for match in _PLACEHOLDER_RE.findall(value):
            errors.append(
                f"V-TENANT-01 {field_name} contains unresolved placeholder "
                f"{match}. Substitute with the concrete value before running "
                "generate."
            )


def _check_auth_consistency(scope: MCPScope, errors: list[str]) -> None:
    """Catch auth-block bugs Pydantic's discriminated union can't.

    The schema enforces shape (which fields belong to which auth type);
    these checks catch *content* bugs in fields that did parse:

    - **V-AUTH-01** scopes_required ⊆ scopes_available — asking the IdP
      for a scope you never declared as available is almost always a
      typo the user wants to know about now, not at consent-screen time.
      Set semantics are used for the subset check, so duplicate entries
      in ``scopes_required`` (e.g. ``["read", "read"]``) are not flagged;
      deduplication is out of scope for this check.
    - **V-AUTH-02** URLs must be absolute and TLS-protected. OAuth and
      OIDC endpoints over plaintext HTTP violate RFC 6749 §3.1 and OIDC
      Core §16.17; allowing ``http://`` through validate would green-light
      a deployment where tokens cross the wire in cleartext.
    """
    auth = scope.auth

    if isinstance(auth, (OAuth2Auth, OIDCAuth)):
        missing = sorted(set(auth.scopes_required) - set(auth.scopes_available.keys()))
        if missing:
            errors.append(
                f"V-AUTH-01 scopes_required contains {missing} not present "
                f"in scopes_available {sorted(auth.scopes_available.keys())}. "
                "Add the scope to scopes_available or drop it from "
                "scopes_required."
            )

    for field_name, value in _auth_url_fields(auth):
        if value is None:
            continue
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        if scheme == "https" and parsed.netloc:
            continue
        if scheme == "http" and parsed.netloc:
            errors.append(
                f"V-AUTH-02 {field_name} uses plaintext http:// "
                f"(got '{value}'). Use https:// — OAuth/OIDC endpoints over "
                "plaintext violate RFC 6749 §3.1 and OIDC Core §16.17."
            )
            continue
        errors.append(
            f"V-AUTH-02 {field_name} is not an absolute https:// URL "
            f"(got '{value}'). An absolute URL with scheme https and a "
            "host is required."
        )

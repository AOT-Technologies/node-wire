# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Build options a spec cannot express: ``--auth`` for a spec with no auth, ``--header``."""

from __future__ import annotations

import re
from typing import Any

AUTH_FORMS = "bearer, header:<name> or query:<name>"
AUTH_SCHEME_NAME = "nw_auth"

_PARAM_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_ENV_REF = re.compile(r"\$\{([A-Za-z0-9_]+)(?::[^}]*)?\}")
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def parse_auth(value: str) -> dict[str, Any]:
    """``--auth`` → the OpenAPI security scheme it stands for. Raises ``ValueError``."""
    raw = value.strip()
    if raw.lower() == "bearer":
        return {"type": "http", "scheme": "bearer"}
    kind, sep, name = raw.partition(":")
    kind = kind.strip().lower()
    name = name.strip()
    if sep and kind in ("header", "query") and _PARAM_NAME.fullmatch(name):
        return {"type": "apiKey", "in": kind, "name": name}
    raise ValueError(f"--auth {value!r}: use {AUTH_FORMS}")


def spec_declares_auth(doc: dict[str, Any]) -> bool:
    """Whether the document declares any security scheme or requirement."""
    if (doc.get("components") or {}).get("securitySchemes") or doc.get("security"):
        return True
    for item in (doc.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        for method in _HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict) and op.get("security"):
                return True
    return False


def apply_auth_override(doc: dict[str, Any], value: str) -> str:
    """Declare ``--auth`` as the document's only security scheme; return a report note.

    Only for documents that declare no auth at all (GitHub's REST spec is one): overriding a
    declared scheme would silently disagree with the spec, so that is refused.
    """
    scheme = parse_auth(value)
    if spec_declares_auth(doc):
        raise ValueError(
            "--auth is only for specs that declare no auth, and this one does; "
            "drop --auth to use the spec's own scheme"
        )
    doc.setdefault("components", {}).setdefault("securitySchemes", {})[AUTH_SCHEME_NAME] = scheme
    doc["security"] = [{AUTH_SCHEME_NAME: []}]
    return f"Auth from --auth {value.strip()}: the spec declares no security scheme"


def parse_header(value: str) -> tuple[str, str]:
    """``--header NAME=VALUE`` → ``(NAME, VALUE)``. Raises ``ValueError``."""
    from node_wire_runtime.rest import validate_static_headers

    name, sep, header_value = value.partition("=")
    name = name.strip()
    if not sep or not name:
        raise ValueError(f"--header {value!r}: use NAME=VALUE, e.g. 'Client-Id=${{CLIENT_ID}}'")
    validate_static_headers({name: header_value})
    return name, header_value


def parse_headers(values: list[str] | None) -> dict[str, str]:
    """Every ``--header``; a repeated name is an error rather than a silent overwrite."""
    headers: dict[str, str] = {}
    for value in values or []:
        name, header_value = parse_header(value)
        if name.lower() in (n.lower() for n in headers):
            raise ValueError(f"--header {name!r} is given more than once")
        headers[name] = header_value
    return headers


def header_env_vars(headers: dict[str, str]) -> dict[str, str]:
    """``${VAR}`` references in header values → ``{VAR: header name}``."""
    found: dict[str, str] = {}
    for name, value in headers.items():
        for match in _ENV_REF.finditer(value):
            found.setdefault(match.group(1), name)
    return found

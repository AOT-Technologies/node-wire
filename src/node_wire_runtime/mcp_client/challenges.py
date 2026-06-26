#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""WWW-Authenticate challenge parsing for MCP OAuth error handling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .discovery import parse_resource_metadata_url

_ERROR_RE = re.compile(r'error\s*=\s*"([^"]+)"', re.IGNORECASE)
_SCOPE_RE = re.compile(r'scope\s*=\s*"([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class WwwAuthenticateChallenge:
    scheme: str
    error: Optional[str]
    error_description: Optional[str]
    resource_metadata: Optional[str]
    scope: Optional[str]
    raw: str

    @property
    def is_invalid_token(self) -> bool:
        return (self.error or "").lower() == "invalid_token"

    @property
    def is_insufficient_scope(self) -> bool:
        return (self.error or "").lower() == "insufficient_scope"

    @property
    def treat_as_unauthorized(self) -> bool:
        return self.is_invalid_token or self.error is None


def parse_www_authenticate(header_value: Optional[str]) -> Optional[WwwAuthenticateChallenge]:
    if not header_value or not header_value.strip():
        return None
    raw = header_value.strip()
    scheme = raw.split(" ", 1)[0] if raw else "Bearer"
    error_match = _ERROR_RE.search(raw)
    scope_match = _SCOPE_RE.search(raw)
    desc_match = re.search(r'error_description\s*=\s*"([^"]+)"', raw, re.IGNORECASE)
    return WwwAuthenticateChallenge(
        scheme=scheme,
        error=error_match.group(1) if error_match else None,
        error_description=desc_match.group(1) if desc_match else None,
        resource_metadata=parse_resource_metadata_url(raw),
        scope=scope_match.group(1) if scope_match else None,
        raw=raw,
    )

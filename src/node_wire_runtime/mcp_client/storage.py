#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Persistence for DCR client registrations (per authorization server issuer)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("runtime.mcp_client.storage")

_ISSUER_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class ClientRegistration:
    """RFC 7591 registration result persisted per issuer."""

    issuer: str
    client_id: str
    client_secret: Optional[str]
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    registered_at: str

    @classmethod
    def from_dict(cls, data: dict) -> ClientRegistration:
        return cls(
            issuer=str(data["issuer"]),
            client_id=str(data["client_id"]),
            client_secret=data.get("client_secret"),
            redirect_uris=tuple(data.get("redirect_uris") or []),
            token_endpoint_auth_method=str(data.get("token_endpoint_auth_method") or "none"),
            registered_at=str(data.get("registered_at") or ""),
        )


def default_registration_store_dir() -> Path:
    return Path.home() / ".node-wire" / "mcp-oauth" / "registrations"


def _issuer_filename(issuer: str) -> str:
    safe = _ISSUER_SAFE.sub("_", issuer.strip()).strip("_") or "issuer"
    return f"{safe}.json"


class RegistrationStore:
    """File-backed store for DCR results; one file per authorization server issuer."""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else default_registration_store_dir()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, issuer: str) -> Path:
        return self._base_dir / _issuer_filename(issuer)

    def get(self, issuer: str) -> Optional[ClientRegistration]:
        path = self._path_for(issuer)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ClientRegistration.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Ignoring corrupt registration file %s: %s", path, exc)
            return None

    def save(self, registration: ClientRegistration) -> None:
        path = self._path_for(registration.issuer)
        payload = asdict(registration)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("Saved DCR registration for issuer", extra={"issuer": registration.issuer})

    def delete(self, issuer: str) -> None:
        path = self._path_for(issuer)
        if path.is_file():
            path.unlink()

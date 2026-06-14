#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, HttpUrl, field_validator

from .egress import HttpEgressBlockedError, validate_host_literal

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class HttpRequestInput(BaseModel):
    action: Literal["request"] = "request"
    url: HttpUrl
    method: str
    headers: Optional[Dict[str, str]] = None
    params: Optional[Dict[str, str]] = None
    body: Optional[Any] = None

    @field_validator("method", mode="before")
    @classmethod
    def normalize_and_validate_method(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("method must be a string")
        normalized = value.strip().upper()
        if normalized not in _ALLOWED_METHODS:
            raise ValueError(f"method must be one of: {', '.join(sorted(_ALLOWED_METHODS))}")
        return normalized

    @field_validator("url")
    @classmethod
    def block_internal_targets(cls, value: HttpUrl) -> HttpUrl:
        parts = urlsplit(str(value))
        host = (parts.hostname or "").strip().lower().rstrip(".")
        try:
            validate_host_literal(host)
        except HttpEgressBlockedError as exc:
            raise ValueError(str(exc)) from exc
        return value


class HttpResponseOutput(BaseModel):
    status_code: int
    headers: Dict[str, str]
    body: Any

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, HttpUrl


class HttpRequestInput(BaseModel):
    action: Literal["request"] = "request"
    url: HttpUrl
    method: str
    headers: Optional[Dict[str, str]] = None
    params: Optional[Dict[str, str]] = None
    body: Optional[Any] = None


class HttpResponseOutput(BaseModel):
    status_code: int
    headers: Dict[str, str]
    body: Any


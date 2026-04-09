from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel
from enum import Enum


class ErrorCategory(str, Enum):
    RETRYABLE = "RETRYABLE"
    BUSINESS = "BUSINESS"
    AUTH = "AUTH"
    FATAL = "FATAL"


class ConnectorResponse(BaseModel):
    """Standardized response model returned by all connectors."""

    success: bool
    data: Optional[Any] = None
    error_code: Optional[str] = None
    error_category: Optional[ErrorCategory] = None
    message: Optional[str] = None
    trace_id: str
    details: Optional[Any] = (
        None  # e.g. validation errors: [{"loc": ["url"], "msg": "...", "type": "..."}]
    )

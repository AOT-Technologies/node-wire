from __future__ import annotations

import httpx

from node_wire_runtime import ErrorCategory, ErrorMapper


# Typical HTTP/network error mappings for the generic HTTP connector.
ErrorMapper.register(httpx.TimeoutException, ErrorCategory.RETRYABLE, code="HTTP_TIMEOUT")
ErrorMapper.register(httpx.ConnectError, ErrorCategory.RETRYABLE, code="HTTP_CONNECT_ERROR")
ErrorMapper.register(httpx.ReadTimeout, ErrorCategory.RETRYABLE, code="HTTP_READ_TIMEOUT")

# Request errors (DNS issues, invalid URLs, etc.) are generally fatal from the client's perspective.
ErrorMapper.register(httpx.RequestError, ErrorCategory.FATAL, code="HTTP_REQUEST_ERROR")

# HTTP status errors are treated as BUSINESS by default; bindings may translate status_code further.
ErrorMapper.register(httpx.HTTPStatusError, ErrorCategory.BUSINESS, code="HTTP_STATUS_ERROR")


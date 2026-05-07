from __future__ import annotations

import httpx

from node_wire_runtime import ErrorCategory, ErrorMapper


# FHIR/Epic error mappings for network and HTTP failures.

# Network timeout and connection errors are retryable.
ErrorMapper.register(httpx.TimeoutException, ErrorCategory.RETRYABLE, code="FHIR_TIMEOUT")
ErrorMapper.register(httpx.ConnectError, ErrorCategory.RETRYABLE, code="FHIR_CONNECT_ERROR")
ErrorMapper.register(httpx.ReadTimeout, ErrorCategory.RETRYABLE, code="FHIR_READ_TIMEOUT")
ErrorMapper.register(httpx.WriteTimeout, ErrorCategory.RETRYABLE, code="FHIR_WRITE_TIMEOUT")

# HTTP status errors are treated as BUSINESS by default.
# The REST API layer or the connectors can provide more specific handling based on status codes.
ErrorMapper.register(httpx.HTTPStatusError, ErrorCategory.BUSINESS, code="FHIR_HTTP_ERROR")

# Request errors (DNS issues, invalid URLs, etc.) are generally fatal.
ErrorMapper.register(httpx.RequestError, ErrorCategory.FATAL, code="FHIR_REQUEST_ERROR")

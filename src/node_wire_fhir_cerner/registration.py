#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import httpx

from node_wire_runtime import ErrorCategory, ErrorMapper


# FHIR/Cerner error mappings for network and HTTP failures.

# Network timeout and connection errors are retryable.
ErrorMapper.register(httpx.TimeoutException, ErrorCategory.RETRYABLE, code="CERNER_TIMEOUT")
ErrorMapper.register(httpx.ConnectError, ErrorCategory.RETRYABLE, code="CERNER_CONNECT_ERROR")
ErrorMapper.register(httpx.ReadTimeout, ErrorCategory.RETRYABLE, code="CERNER_READ_TIMEOUT")
ErrorMapper.register(httpx.WriteTimeout, ErrorCategory.RETRYABLE, code="CERNER_WRITE_TIMEOUT")

# HTTP status errors are treated as BUSINESS by default.
# The REST API layer or the connectors can provide more specific handling based on status codes.
ErrorMapper.register(httpx.HTTPStatusError, ErrorCategory.BUSINESS, code="CERNER_HTTP_ERROR")

# Register ValueError as BUSINESS to cover validation guards and wrapped Cerner errors.
ErrorMapper.register(ValueError, ErrorCategory.BUSINESS, code="CERNER_VALIDATION_ERROR")

# Request errors (DNS issues, invalid URLs, etc.) are generally fatal.
ErrorMapper.register(httpx.RequestError, ErrorCategory.FATAL, code="CERNER_REQUEST_ERROR")

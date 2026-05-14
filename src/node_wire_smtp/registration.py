#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import aiosmtplib

from node_wire_runtime import ErrorCategory, ErrorMapper


# Connection / timeout issues are retryable.
ErrorMapper.register(
    aiosmtplib.errors.SMTPConnectError, ErrorCategory.RETRYABLE, code="SMTP_CONNECT_ERROR"
)
ErrorMapper.register(
    aiosmtplib.errors.SMTPTimeoutError, ErrorCategory.RETRYABLE, code="SMTP_TIMEOUT"
)

# Authentication failures map to AUTH.
ErrorMapper.register(
    aiosmtplib.errors.SMTPAuthenticationError, ErrorCategory.AUTH, code="SMTP_AUTH_ERROR"
)

# Generic SMTP protocol problems are treated as BUSINESS by default.
ErrorMapper.register(aiosmtplib.errors.SMTPException, ErrorCategory.BUSINESS, code="SMTP_ERROR")

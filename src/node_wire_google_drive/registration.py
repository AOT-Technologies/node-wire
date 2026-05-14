#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from node_wire_runtime import ErrorCategory, ErrorMapper

from .exceptions import (
    GoogleDriveAuthError,
    GoogleDriveBusinessError,
    GoogleDriveFatalError,
    GoogleDriveRateLimitError,
)

ErrorMapper.register(GoogleDriveAuthError, ErrorCategory.AUTH, code="GDRIVE_AUTH")
ErrorMapper.register(GoogleDriveRateLimitError, ErrorCategory.RETRYABLE, code="GDRIVE_RATE_LIMIT")
ErrorMapper.register(GoogleDriveBusinessError, ErrorCategory.BUSINESS, code="GDRIVE_BUSINESS_RULE")
ErrorMapper.register(GoogleDriveFatalError, ErrorCategory.FATAL, code="GDRIVE_FATAL")

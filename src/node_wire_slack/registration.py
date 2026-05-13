"""
ErrorMapper registrations for the Slack connector.

Mirrors node_wire_google_drive/registration.py — registers domain exceptions
from exceptions.py so the runtime can translate them into the standard
ConnectorResponse error taxonomy.
"""
from __future__ import annotations

from node_wire_runtime import ErrorCategory, ErrorMapper

from .exceptions import (
    SlackAuthError,
    SlackMessageError,
    SlackPermissionError,
    SlackRateLimitError,
    SlackUploadError,
)

# Auth failures — token is invalid or revoked.
ErrorMapper.register(SlackAuthError, ErrorCategory.AUTH, code="SLACK_AUTH_ERROR")

# Permission failures — token lacks the required OAuth scope.
ErrorMapper.register(SlackPermissionError, ErrorCategory.AUTH, code="SLACK_PERMISSION_ERROR")

# Rate-limit — eligible for automatic retry by the runtime.
ErrorMapper.register(SlackRateLimitError, ErrorCategory.RETRYABLE, code="SLACK_RATE_LIMIT")

# Upload failures — bad content, missing fields, or Slack API error during upload.
ErrorMapper.register(SlackUploadError, ErrorCategory.BUSINESS, code="SLACK_UPLOAD_ERROR")

# Message failures — channel not found, payload rejected, etc.
ErrorMapper.register(SlackMessageError, ErrorCategory.BUSINESS, code="SLACK_MESSAGE_ERROR")

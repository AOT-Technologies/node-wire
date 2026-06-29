#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Domain exception hierarchy for the Slack connector.

These exceptions are raised by logic.py and mapped to ErrorCategory codes
by registration.py via ErrorMapper.
"""

from __future__ import annotations


class SlackAuthError(Exception):
    """Raised when the bot token is invalid, revoked, or the account is inactive."""


class SlackPermissionError(Exception):
    """Raised when the token lacks the required OAuth scope for the operation."""


class SlackRateLimitError(Exception):
    """Raised on HTTP 429 or Slack's `ratelimited` error — eligible for retry."""


class SlackUploadError(Exception):
    """Raised when a file upload step fails (bad content, missing fields, Slack error)."""


class SlackMessageError(Exception):
    """Raised when a chat.postMessage call fails for a business-logic reason."""

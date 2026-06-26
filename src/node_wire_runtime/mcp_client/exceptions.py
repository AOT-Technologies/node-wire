#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Exceptions for outbound MCP OAuth client (spec 2025-11-25)."""

from __future__ import annotations


class McpOAuthError(Exception):
    """Base error for MCP outbound OAuth client operations."""


class McpOAuthDiscoveryError(McpOAuthError):
    """Protected resource or authorization server metadata discovery failed."""


class McpOAuthRegistrationError(McpOAuthError):
    """Dynamic client registration (RFC 7591) failed."""


class McpOAuthFlowAborted(McpOAuthError):
    """User authorization flow aborted or rejected (e.g. state mismatch)."""


class McpOAuthSecurityError(McpOAuthError):
    """Security violation during OAuth (CSRF, redirect, PKCE, audience)."""


class McpTokenRefreshError(McpOAuthError):
    """Token refresh failed; caller should restart authorization code flow."""


class McpAudienceMismatch(McpOAuthSecurityError):
    """JWT access token audience does not match target MCP server URL."""


class McpOAuthConfigurationError(McpOAuthError):
    """Operator configuration is incomplete or invalid."""

#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Backward-compatible re-export.

The per-identity sliding-window limiter used to live here as a REST-only
concern. It moved to ``node_wire_runtime.rate_limit`` (2026-09-01, M-2 fix) so
MCP and gRPC can share the same opt-in mechanism instead of each transport
keeping its own. Import from ``node_wire_runtime.rate_limit`` in new code;
this module is kept so existing imports of
``bindings.rest_api.rate_limit.InMemoryRateLimiter``/``RateLimitResult`` keep
working.
"""

from __future__ import annotations

from node_wire_runtime.rate_limit import InMemoryRateLimiter, RateLimitResult

__all__ = ["InMemoryRateLimiter", "RateLimitResult"]

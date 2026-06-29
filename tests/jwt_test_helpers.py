#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
import time
from typing import Any

import jwt

from node_wire_runtime.caller_identity import JWT_AUDIENCE_ENV, JWT_ISSUER_ENV


def mint_test_jwt(payload: dict[str, Any], secret: str) -> str:
    """Mint an HS256 JWT with required exp/iat/aud/iss for binding ingress tests."""
    audience = os.environ[JWT_AUDIENCE_ENV]
    issuer = os.environ[JWT_ISSUER_ENV]
    now = int(time.time())
    full = {
        "iat": now,
        "exp": now + 3600,
        "aud": audience,
        "iss": issuer,
        **payload,
    }
    return jwt.encode(full, secret, algorithm="HS256")

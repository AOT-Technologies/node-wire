#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import time

import jwt
import pytest

from node_wire_runtime.caller_identity import (
    JWT_AUDIENCE_ENV,
    JWT_ISSUER_ENV,
    decode_binding_jwt,
    verify_bearer_token_and_identity,
)
from tests.jwt_test_helpers import mint_test_jwt


@pytest.fixture
def jwt_secret() -> str:
    return "jwt-validation-test-secret-at-least-32b"


def test_decode_binding_jwt_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    token = mint_test_jwt({"sub": "alice", "scopes": ["mcp:test"]}, jwt_secret)
    claims = decode_binding_jwt(token, jwt_secret)
    assert claims["sub"] == "alice"


def test_decode_binding_jwt_rejects_missing_exp(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "alice",
            "iat": now,
            "aud": "node-wire-test",
            "iss": "node-wire-test-issuer",
        },
        jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        decode_binding_jwt(token, jwt_secret)


def test_decode_binding_jwt_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    past = int(time.time()) - 7200
    token = jwt.encode(
        {
            "sub": "alice",
            "iat": past,
            "exp": past + 60,
            "aud": "node-wire-test",
            "iss": "node-wire-test-issuer",
        },
        jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        decode_binding_jwt(token, jwt_secret)


def test_decode_binding_jwt_rejects_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "alice",
            "iat": now,
            "exp": now + 3600,
            "aud": "wrong-audience",
            "iss": "node-wire-test-issuer",
        },
        jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        decode_binding_jwt(token, jwt_secret)


def test_decode_binding_jwt_rejects_when_audience_issuer_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    token = mint_test_jwt({"sub": "alice"}, jwt_secret)
    monkeypatch.delenv(JWT_AUDIENCE_ENV, raising=False)
    monkeypatch.delenv(JWT_ISSUER_ENV, raising=False)
    with pytest.raises(jwt.PyJWTError):
        decode_binding_jwt(token, jwt_secret)


def test_verify_bearer_token_and_identity_jwt_path(
    monkeypatch: pytest.MonkeyPatch,
    jwt_secret: str,
) -> None:
    monkeypatch.setenv(JWT_AUDIENCE_ENV, "node-wire-test")
    monkeypatch.setenv(JWT_ISSUER_ENV, "node-wire-test-issuer")

    token = mint_test_jwt({"sub": "bob", "scopes": ["mcp:a"]}, jwt_secret)
    ok, identity = verify_bearer_token_and_identity(
        token,
        api_key=None,
        jwt_secret=jwt_secret,
        api_key_scopes_env="NW_REST_API_KEY_SCOPES",
        api_key_auth_type="rest_api_key",
    )
    assert ok is True
    assert identity is not None
    assert identity.principal == "bob"
    assert identity.scopes == ("mcp:a",)

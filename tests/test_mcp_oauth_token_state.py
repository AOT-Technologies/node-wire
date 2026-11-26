#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""TokenManager lifecycle across restarts, forced refresh, scope step-up and audience."""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Awaitable, Callable, Optional

import httpx
import jwt
import pytest

from node_wire_runtime.mcp_client.client import McpOAuthClient
from node_wire_runtime.mcp_client.config import (
    AuthClientConfig,
    AuthConfig,
    AuthRedirectConfig,
    McpClientConfig,
    McpServerConfig,
    RedirectMode,
)
from node_wire_runtime.mcp_client.discovery import (
    AuthorizationServerMetadata,
    DiscoveryResult,
    ProtectedResourceMetadata,
)
from node_wire_runtime.mcp_client.exceptions import (
    McpAudienceMismatch,
    McpOAuthSecurityError,
)
from node_wire_runtime.mcp_client.oauth_flow import AuthorizationCodeFlow, OAuthTokenSet
from node_wire_runtime.mcp_client.storage import ClientRegistration
from node_wire_runtime.mcp_client.token_manager import (
    McpRecoveryAction,
    TokenManager,
    TokenState,
    compute_token_state,
)
from node_wire_runtime.mcp_client.token_storage import (
    InMemoryTokenStore,
    StoredOAuthTokens,
    stored_from_oauth_response,
)

MCP = "https://mcp.example.com/mcp"
ISSUER = "https://issuer.example"


def _discovery() -> DiscoveryResult:
    return DiscoveryResult(
        mcp_server_url=MCP,
        protected_resource=ProtectedResourceMetadata(
            resource=MCP, authorization_servers=(ISSUER,), raw={}
        ),
        authorization_server=AuthorizationServerMetadata(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/authorize",
            token_endpoint=f"{ISSUER}/token",
            registration_endpoint=None,
            scopes_supported=None,
            raw={},
        ),
        issuer=ISSUER,
    )


def _reg(client_id: str = "dcr-client") -> ClientRegistration:
    return ClientRegistration(
        issuer=ISSUER,
        client_id=client_id,
        client_secret=None,
        redirect_uris=("http://127.0.0.1:1/callback",),
        token_endpoint_auth_method="none",
        registered_at="",
    )


def _config(**auth) -> McpClientConfig:
    # No auth.client.id: the client id only exists as a DCR registration.
    return McpClientConfig(server=McpServerConfig(url=MCP), auth=AuthConfig(**auth))


def _tokens(
    access: str = "at",
    *,
    expires_in: Optional[int] = 3600,
    refresh: Optional[str] = "rt",
    registration: Optional[ClientRegistration] = None,
) -> StoredOAuthTokens:
    stored = stored_from_oauth_response(
        user_id="alice",
        mcp_server_url=MCP,
        issuer=ISSUER,
        access_token=access,
        token_type="Bearer",
        expires_in=expires_in,
        refresh_token=refresh,
        scope=None,
    )
    stored.client_registration = registration
    return stored


def _token_set(access: str, refresh: Optional[str] = "rt") -> OAuthTokenSet:
    return OAuthTokenSet(
        access_token=access,
        token_type="Bearer",
        expires_in=3600,
        refresh_token=refresh,
        scope=None,
        raw={},
    )


async def _no_browser() -> OAuthTokenSet:
    raise AssertionError("re-authorization must not run")


def _manager(
    store: InMemoryTokenStore,
    *,
    config: Optional[McpClientConfig] = None,
    flow: Optional[AuthorizationCodeFlow] = None,
    reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = _no_browser,
) -> TokenManager:
    config = config or _config()
    return TokenManager(
        config,
        user_id="alice",
        token_store=store,
        discovery=_discovery(),
        auth_flow=flow or AuthorizationCodeFlow(config, discovery=_discovery()),
        reauthorize=reauthorize,
    )


def _token_endpoint(posts: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(dict(urllib.parse.parse_qsl(request.content.decode())))
        return httpx.Response(
            200,
            json={"access_token": "refreshed", "expires_in": 3600, "refresh_token": "rt2"},
        )

    return httpx.MockTransport(handler)


# --- registration persisted with tokens ------------------------------------


@pytest.mark.asyncio
async def test_restart_then_expiry_refreshes_without_browser() -> None:
    store = InMemoryTokenStore()

    # Process 1: authorize through a flow that resolved a DCR registration.
    first_flow = AuthorizationCodeFlow(_config(), discovery=_discovery(), registration=_reg())

    async def authorize() -> OAuthTokenSet:
        return _token_set("first")

    first = _manager(store, flow=first_flow, reauthorize=authorize)
    assert await first.get_bearer_token() == "first"

    # Process 2: fresh manager + flow (no in-process authorization), token expired.
    stored = store.get(first.partition_key)
    assert stored is not None
    stored.expires_at = time.time() - 10
    second = _manager(store)
    posts: list[dict] = []
    async with httpx.AsyncClient(transport=_token_endpoint(posts)) as http:
        token = await second.get_bearer_token(http_client=http)

    assert token == "refreshed"
    assert posts and posts[0]["client_id"] == "dcr-client"
    assert store.get(second.partition_key).client_registration == _reg()  # type: ignore[union-attr]


def test_old_token_record_without_registration_still_loads() -> None:
    old = {
        "user_id": "alice",
        "mcp_server_url": MCP,
        "issuer": ISSUER,
        "access_token": "at",
        "token_type": "Bearer",
        "refresh_token": "rt",
        "scope": None,
        "expires_at": None,
        "updated_at": 1.0,
    }
    loaded = StoredOAuthTokens.from_dict(old)
    assert loaded.client_registration is None
    round_trip = StoredOAuthTokens.from_dict(json.loads(json.dumps(loaded.to_dict())))
    assert round_trip == loaded

    with_reg = _tokens(registration=_reg())
    assert StoredOAuthTokens.from_dict(json.loads(json.dumps(with_reg.to_dict()))) == with_reg


# --- token state -------------------------------------------------------------


def test_compute_token_state() -> None:
    reg = _reg()
    assert compute_token_state(None, reg, lead_seconds=60) is TokenState.NONE
    assert compute_token_state(_tokens(), reg, lead_seconds=60) is TokenState.VALID
    assert compute_token_state(_tokens(expires_in=None), reg, lead_seconds=60) is TokenState.VALID
    assert compute_token_state(_tokens(expires_in=30), reg, lead_seconds=60) is (
        TokenState.NEAR_EXPIRY
    )
    expired = _tokens()
    expired.expires_at = time.time() - 1
    assert compute_token_state(expired, reg, lead_seconds=60) is TokenState.REFRESHABLE
    assert compute_token_state(expired, None, lead_seconds=60) is TokenState.NEEDS_REAUTH
    no_rt = _tokens(refresh=None)
    no_rt.expires_at = time.time() - 1
    assert compute_token_state(no_rt, reg, lead_seconds=60) is TokenState.NEEDS_REAUTH


@pytest.mark.asyncio
async def test_near_expiry_without_refresh_token_uses_current_token() -> None:
    """A token still valid for 30 s is used rather than opening a browser."""
    store = InMemoryTokenStore()
    mgr = _manager(store)
    mgr.save_tokens(_tokens("still-good", expires_in=30, refresh=None))
    assert await mgr.get_bearer_token() == "still-good"


@pytest.mark.asyncio
async def test_force_refresh_uses_refresh_token_first() -> None:
    store = InMemoryTokenStore()
    mgr = _manager(store)
    mgr.save_tokens(_tokens("valid", registration=_reg()))
    posts: list[dict] = []
    async with httpx.AsyncClient(transport=_token_endpoint(posts)) as http:
        token = await mgr.get_bearer_token(force_refresh=True, http_client=http)
    assert token == "refreshed"
    assert [p["grant_type"] for p in posts] == ["refresh_token"]


# --- recovery actions and scope step-up --------------------------------------


@pytest.mark.asyncio
async def test_recovery_actions_are_enum_members() -> None:
    mgr = _manager(InMemoryTokenStore())
    assert await mgr.handle_mcp_response(200, None) is McpRecoveryAction.OK
    assert await mgr.handle_mcp_response(403, None) is McpRecoveryAction.FORBIDDEN
    assert (
        await mgr.handle_mcp_response(401, 'Bearer error="invalid_token"')
        is McpRecoveryAction.RETRY
    )
    assert (
        await mgr.handle_mcp_response(403, 'Bearer error="insufficient_scope", scope="a b"')
        is McpRecoveryAction.REAUTHORIZE
    )


@pytest.mark.asyncio
async def test_step_up_scopes_reach_the_authorization_request() -> None:
    flow = AuthorizationCodeFlow(_config(scopes="a"), discovery=_discovery(), registration=_reg())
    mgr = _manager(InMemoryTokenStore(), config=_config(scopes="a"), flow=flow)
    await mgr.handle_mcp_response(401, 'Bearer error="insufficient_scope", scope="a b"')
    _, url = await flow.prepare_authorization_session(redirect_uri="http://127.0.0.1:1/callback")
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert qs["scope"] == ["a b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_insufficient_scope_forces_new_authorization(status: int) -> None:
    store = InMemoryTokenStore()
    reauth_calls = 0

    async def authorize() -> OAuthTokenSet:
        nonlocal reauth_calls
        reauth_calls += 1
        return _token_set("stepped-up")

    mgr = _manager(store, reauthorize=authorize)
    mgr.save_tokens(_tokens("narrow", registration=_reg()))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        auth = request.headers["Authorization"]
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        seen.append(auth)
        if auth == "Bearer narrow":
            return httpx.Response(
                status,
                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="a b"'},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = McpOAuthClient(
        MCP, config=_config(), user_id="alice", token_manager=mgr, http_client=http
    )
    assert await client.list_tools() == []
    await http.aclose()
    assert reauth_calls == 1
    assert seen == ["Bearer narrow", "Bearer stepped-up"]


# --- audience ------------------------------------------------------------------


def _jwt(aud: str) -> str:
    return jwt.encode({"aud": aud}, "k" * 32, algorithm="HS256")


@pytest.mark.asyncio
async def test_wrong_audience_token_is_not_saved() -> None:
    store = InMemoryTokenStore()

    async def authorize() -> OAuthTokenSet:
        return _token_set(_jwt("https://other.example/mcp"))

    mgr = _manager(store, reauthorize=authorize)
    with pytest.raises(McpAudienceMismatch):
        await mgr.get_bearer_token()
    assert store.get(mgr.partition_key) is None


@pytest.mark.asyncio
async def test_stored_wrong_audience_token_is_discarded() -> None:
    store = InMemoryTokenStore()
    good = _jwt(MCP)

    async def authorize() -> OAuthTokenSet:
        return _token_set(good)

    mgr = _manager(store, reauthorize=authorize)
    store.save(_tokens(_jwt("https://other.example/mcp")))
    assert await mgr.get_bearer_token() == good
    assert store.get(mgr.partition_key).access_token == good  # type: ignore[union-attr]


# --- pending authorization sessions ---------------------------------------------


def _configured_url_flow() -> AuthorizationCodeFlow:
    cfg = McpClientConfig(
        server=McpServerConfig(url=MCP),
        auth=AuthConfig(
            client=AuthClientConfig(id="cid"),
            redirect=AuthRedirectConfig(
                mode=RedirectMode.CONFIGURED_URL, url="https://app.example/cb"
            ),
        ),
    )
    return AuthorizationCodeFlow(cfg, discovery=_discovery())


def _state(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]


def _code_endpoint() -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, json={"access_token": "at", "expires_in": 60})
    )


@pytest.mark.asyncio
async def test_two_pending_authorizations_both_complete(monkeypatch) -> None:
    flow = _configured_url_flow()
    first = _state(await flow.start_authorization())
    second = _state(await flow.start_authorization())
    real_client = httpx.AsyncClient

    def mocked(*args, **kwargs):
        kwargs["transport"] = _code_endpoint()
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    for state in (first, second):
        tokens = await flow.complete_authorization_with_callback_url(
            f"https://app.example/cb?code=c&state={state}"
        )
        assert tokens.access_token == "at"
    # A session is consumed by its callback.
    with pytest.raises(McpOAuthSecurityError):
        await flow.complete_authorization_with_callback_url(
            f"https://app.example/cb?code=c&state={first}"
        )


@pytest.mark.asyncio
async def test_pending_authorization_expires(monkeypatch) -> None:
    from node_wire_runtime.mcp_client import oauth_flow

    flow = _configured_url_flow()
    state = _state(await flow.start_authorization())
    now = time.monotonic()
    monkeypatch.setattr(
        oauth_flow.time, "monotonic", lambda: now + oauth_flow.PENDING_SESSION_TTL_SECONDS + 1
    )
    with pytest.raises(McpOAuthSecurityError):
        await flow.complete_authorization_with_callback_url(
            f"https://app.example/cb?code=c&state={state}"
        )


# --- discovery cache -------------------------------------------------------------


def test_discovery_cache_is_shared_per_config() -> None:
    from node_wire_runtime.mcp_client.discovery import discovery_cache_for_config

    assert discovery_cache_for_config(_config()) is discovery_cache_for_config(_config())


@pytest.mark.asyncio
async def test_token_manager_rediscovers_after_cache_expiry(monkeypatch) -> None:
    from node_wire_runtime.mcp_client import discovery as discovery_mod
    from node_wire_runtime.mcp_client.discovery import discovery_cache_for_config

    calls = 0

    async def fake_prm(client, url, *, www_authenticate=None):
        nonlocal calls
        calls += 1
        return _discovery().protected_resource

    async def fake_as(client, issuer):
        return _discovery().authorization_server

    monkeypatch.setattr(discovery_mod, "fetch_protected_resource_metadata", fake_prm)
    monkeypatch.setattr(discovery_mod, "fetch_authorization_server_metadata", fake_as)
    config = McpClientConfig(server=McpServerConfig(url="https://rediscover.example/mcp"))
    mgr = TokenManager(config, user_id="alice", token_store=InMemoryTokenStore())
    await mgr.ensure_discovery()
    await mgr.ensure_discovery()
    assert calls == 1
    discovery_cache_for_config(config).invalidate(config.canonical_server_url)
    await mgr.ensure_discovery()
    assert calls == 2

# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Run every action against a mock HTTP transport, so no OpenSanctions key is needed (CI has none)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from node_wire_opensanctions.logic import OpensanctionsConnector
from node_wire_runtime import rest
from node_wire_runtime.auth import StaticTokenAuthProvider
from node_wire_runtime.models import ErrorCategory

QUERIES = {"q1": {"schema": "Person", "properties": {"name": ["Viktor Bout"]}}}

# action, input, method, path
ROWS: list[tuple[str, dict[str, Any], str, str]] = [
    (
        "match_match_dataset_post",
        {"dataset": "default", "queries": QUERIES},
        "POST",
        "/match/default",
    ),
    (
        "search_search_dataset_get",
        {"dataset": "sanctions", "q": "Bout"},
        "GET",
        "/search/sanctions",
    ),
    ("fetch_entity_entities_entity_id_get", {"entity_id": "Q1"}, "GET", "/entities/Q1"),
    (
        "fetch_adjacent_entities_entities_entity_id",
        {"entity_id": "Q1"},
        "GET",
        "/entities/Q1/adjacent",
    ),
    (
        "fetch_adjacent_by_property_entities_entity_id",
        {"entity_id": "Q1", "property_name": "sanctions"},
        "GET",
        "/entities/Q1/adjacent/sanctions",
    ),
    ("reconcile_reconcile_dataset_get", {"dataset": "default"}, "GET", "/reconcile/default"),
    ("healthz_healthz_get", {}, "GET", "/healthz"),
    ("readyz_readyz_get", {}, "GET", "/readyz"),
    ("catalog_catalog_get", {}, "GET", "/catalog"),
    ("algorithms_algorithms_get", {}, "GET", "/algorithms"),
    ("force_update_updatez_post", {}, "POST", "/updatez"),
    ("statements_statements_get", {}, "GET", "/statements"),
]


class Secrets:
    def get_secret(self, key: str) -> str:
        return {"OPENSANCTIONS_API_KEY": "key-123"}[key]


class Wire:
    """What the mock OpenSanctions saw, and what it answers next."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.json: Any = {"ok": True}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.json)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def body(self) -> Any:
        return json.loads(self.last.content) if self.last.content else None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()
    client = httpx.AsyncClient(transport=httpx.MockTransport(w.handler))

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr(rest, "_shared_client", lambda trust_env: client)
    monkeypatch.setattr(rest, "assert_safe_destination", allow)
    return w


def make() -> OpensanctionsConnector:
    secrets = Secrets()
    return OpensanctionsConnector(
        secret_provider=secrets,
        auth_provider=StaticTokenAuthProvider(
            secret_provider=secrets,
            secret_key="OPENSANCTIONS_API_KEY",
            header_name="Authorization",
            prefix="ApiKey",
        ),
    )


def test_rows_cover_every_action() -> None:
    assert {r[0] for r in ROWS} == set(OpensanctionsConnector.nw_action_metas())


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "inp", "method", "path"), ROWS, ids=[r[0] for r in ROWS])
async def test_action_sends_the_documented_request(
    wire: Wire, action: str, inp: dict[str, Any], method: str, path: str
) -> None:
    result = await make().run({"action": action, **inp})
    assert result.success, result.message
    request = wire.last
    assert (request.method, request.url.path) == (method, path)
    assert request.url.host == "api.opensanctions.org"
    assert request.headers["Authorization"] == "ApiKey key-123"


@pytest.mark.asyncio
async def test_match_sends_the_queries_as_the_body_and_the_options_in_the_query(wire: Wire) -> None:
    await make().run(
        {
            "action": "match_match_dataset_post",
            "dataset": "default",
            "queries": QUERIES,
            "threshold": 0.8,
            "limit": 3,
        }
    )
    assert wire.body["queries"] == QUERIES
    assert wire.last.url.params["threshold"] == "0.8"
    assert wire.last.url.params["limit"] == "3"


@pytest.mark.asyncio
async def test_a_path_value_is_quoted(wire: Wire) -> None:
    await make().run({"action": "fetch_entity_entities_entity_id_get", "entity_id": "a/b"})
    assert wire.last.url.raw_path.split(b"?")[0] == b"/entities/a%2Fb"


@pytest.mark.asyncio
async def test_the_answer_is_returned_as_data(wire: Wire) -> None:
    wire.json = {"status": "ok"}
    result = await make().run({"action": "healthz_healthz_get"})
    assert result.success and result.data["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [(401, ErrorCategory.AUTH), (404, ErrorCategory.BUSINESS), (422, ErrorCategory.BUSINESS)],
)
async def test_the_status_decides_the_error_category(
    wire: Wire, status: int, category: ErrorCategory
) -> None:
    wire.status = status
    result = await make().run({"action": "healthz_healthz_get"})
    assert not result.success and result.error_category == category


def test_the_lending_profile_is_only_match_and_fetch_entity() -> None:
    """PRD scope: the lending agent screens and reads an entity; no writes, no probes."""
    path = Path(__file__).resolve().parents[4] / "expose/opensanctions/opensanctions-lending.yaml"
    actions = set(yaml.safe_load(path.read_text(encoding="utf-8"))["actions"])
    assert actions == {"match_match_dataset_post", "fetch_entity_entities_entity_id_get"}
    assert actions <= set(OpensanctionsConnector.nw_action_metas())

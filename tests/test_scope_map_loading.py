#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""load_scope_map_from_env runs on every MCP request: it must not re-read .env or
re-parse the same JSON each time, but must still follow env changes."""

from __future__ import annotations

import pytest

from node_wire_runtime.policies import mcp_scope_policy as msp


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setattr(msp, "_scope_dotenv_loaded", False)
    msp._parse_scope_map.cache_clear()
    yield
    msp._parse_scope_map.cache_clear()


def test_dotenv_loaded_once_when_map_unset(monkeypatch) -> None:
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.setenv("NW_REST_LOAD_DOTENV", "true")
    calls: list[object] = []
    monkeypatch.setattr(msp, "load_dotenv", lambda *a, **k: calls.append(a))

    assert msp.load_scope_map_from_env() == {}
    assert msp.load_scope_map_from_env() == {}

    assert len(calls) == 2  # cwd .env + repo-root .env, first call only


def test_dotenv_skipped_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.setenv("NW_REST_LOAD_DOTENV", "false")
    calls: list[object] = []
    monkeypatch.setattr(msp, "load_dotenv", lambda *a, **k: calls.append(a))

    assert msp.load_scope_map_from_env() == {}
    assert calls == []


def test_parsed_once_and_returns_fresh_dict(monkeypatch) -> None:
    monkeypatch.setenv("NW_MCP_ACTION_SCOPE_MAP_JSON", '{"slack.post_message": "chat:write"}')

    first = msp.load_scope_map_from_env()
    first["mutated"] = "x"
    second = msp.load_scope_map_from_env()

    assert second == {"slack.post_message": "chat:write"}
    assert msp._parse_scope_map.cache_info().hits == 1


def test_env_change_is_picked_up(monkeypatch) -> None:
    monkeypatch.setenv("NW_MCP_ACTION_SCOPE_MAP_JSON", '{"a.b": "s1"}')
    assert msp.load_scope_map_from_env() == {"a.b": "s1"}
    monkeypatch.setenv("NW_MCP_ACTION_SCOPE_MAP_JSON", '{"a.b": "s2"}')
    assert msp.load_scope_map_from_env() == {"a.b": "s2"}


@pytest.mark.parametrize("raw", ['["not", "an", "object"]', '{"a.b": 1}'])
def test_invalid_map_raises_every_time(monkeypatch, raw) -> None:
    monkeypatch.setenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raw)
    for _ in range(2):
        with pytest.raises(ValueError):
            msp.load_scope_map_from_env()

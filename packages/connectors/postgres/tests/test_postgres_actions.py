# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Run every action against a fake pool, so no database is needed (CI has none)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from node_wire_postgres import policy
from node_wire_postgres.logic import PostgresConnector
from node_wire_postgres.policy import PostgresPolicyError

ROWS = [
    {
        "id": 1,
        "price": Decimal("1.50"),
        "at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "raw": b"\x01",
        "ids": [UUID(int=1)],
        "doc": {"a": (1, 2)},
        "none": None,
    }
]


class FakeCopy:
    def __init__(self, blocks: list[bytes]) -> None:
        self.blocks, self.written = blocks, []

    async def __aenter__(self) -> FakeCopy:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def write_row(self, row: list[Any]) -> None:
        self.written.append(row)

    async def __aiter__(self):
        for block in self.blocks:
            yield block


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.description = [SimpleNamespace(name=k) for k in ROWS[0]]
        self.rowcount = 3

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> None:
        self.conn.statements.append((query, params))
        self.conn.execute_kwargs.append(kwargs)
        if not self.conn.has_result:
            self.description = None

    async def fetchmany(self, size: int) -> list[dict[str, Any]]:
        return (ROWS * (size if self.conn.many else 1))[:size]

    def copy(self, query: Any, params: Any = None) -> FakeCopy:
        self.conn.statements.append((query, params))
        return FakeCopy(self.conn.blocks)


class FakeConn:
    def __init__(self) -> None:
        self.statements: list[tuple[Any, Any]] = []
        self.autocommit = False
        self.rolled_back = False
        self.execute_kwargs: list[dict[str, Any]] = []
        self.has_result = True
        self.many = False
        self.blocks = [b"a,b\n", b"1,2\n"]

    async def execute(self, query: Any, params: Any = None) -> None:
        self.statements.append((query, params))

    async def set_autocommit(self, value: bool) -> None:
        self.autocommit = value

    async def rollback(self) -> None:
        self.rolled_back = True

    def cursor(self, name: str | None = None, row_factory: Any = None) -> FakeCursor:
        return FakeCursor(self)


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self.conn


class Secrets:
    def get_secret(self, key: str) -> str:
        return {"POSTGRES_USERNAME": "u", "POSTGRES_PASSWORD": "p"}[key]


CONFIG = {"host": "h", "port": 5432, "database": "d", "mode": "admin"}


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> FakeConn:
    fake = FakeConn()

    async def pool(connector: Any, s: Any) -> FakePool:
        return FakePool(fake)

    monkeypatch.setattr(policy, "_pool", pool)
    return fake


def make(**config: Any) -> PostgresConnector:
    return PostgresConnector(secret_provider=Secrets(), config={**CONFIG, **config})


T = {"schema_name": "shop", "table": "orders"}
SQL = {"sql": "select * from shop.orders where id = %s", "params": [1]}

PAYLOADS: dict[str, dict[str, Any]] = {
    "ping": {},
    "server_version": {},
    "who_am_i": {},
    "list_databases": {},
    "show_settings": {"pattern": "work_mem"},
    "active_sessions": {"include_idle": True},
    "list_locks": {"only_waiting": True},
    "cancel_backend": {"pid": 42},
    "terminate_backend": {"pid": 42},
    "list_schemas": {"include_system": True},
    "list_tables": {"schema_name": "shop", "kinds": ["table", "view"]},
    "describe_table": T,
    "list_columns": T,
    "list_indexes": T,
    "list_constraints": T,
    "list_foreign_keys": T,
    "list_sequences": {"schema_name": "shop"},
    "list_functions": {"schema_name": "shop", "kind": "function"},
    "get_function_definition": {"schema_name": "shop", "name": "order_count"},
    "list_triggers": T,
    "list_extensions": {},
    "list_types": {"schema_name": "shop"},
    "list_roles": {},
    "list_privileges": T,
    "table_size": T,
    "query": SQL,
    "query_one": SQL,
    "count": {**T, "where": {"status": "paid", "id": [1, 2], "note": None}},
    "exists": {**T, "where": {"id": 1}},
    "sample_rows": {**T, "limit": 5},
    "explain": {"sql": "select 1", "analyze": True},
    "insert": {**T, "rows": [{"total": 1, "meta": {"k": "v"}}], "returning": ["id"]},
    "upsert": {
        **T,
        "rows": [{"id": 1, "total": 2}],
        "conflict_columns": ["id"],
        "update_columns": ["total"],
        "returning": ["id"],
    },
    "update": {**T, "values": {"status": "paid"}, "where": {"id": 1}, "returning": ["id"]},
    "delete": {**T, "where": {"id": 1}, "returning": ["id"]},
    "execute": {"sql": "update shop.orders set status = %s", "params": ["x"]},
    "transaction": {"statements": [{"sql": "select 1"}, {"sql": "select %s", "params": [1]}]},
    "copy_in": {**T, "columns": ["id", "total"], "rows": [[1, 2], [3, {"a": 1}]]},
    "copy_out": {"sql": "select id from shop.orders", "params": None},
    "call_function": {"schema_name": "shop", "name": "order_count", "args": [1]},
    "call_procedure": {"schema_name": "shop", "name": "close_old_orders", "args": [30]},
    "create_schema": {"name": "scratch", "if_not_exists": True},
    "drop_schema": {"name": "scratch", "cascade": True, "if_exists": True},
    "create_table": {
        "schema_name": "scratch",
        "table": "t",
        "columns": [
            {"name": "id", "type": "serial", "nullable": False},
            {"name": "n", "type": "varchar(20)", "unique": True, "default": "x"},
        ],
        "primary_key": ["id"],
        "if_not_exists": True,
    },
    "alter_table": {**T, "operation": "add_column", "column": "c", "column_type": "text"},
    "drop_table": {**T, "cascade": True, "if_exists": True},
    "truncate": {**T, "restart_identity": True, "cascade": True},
    "create_index": {**T, "columns": ["id"], "unique": True, "concurrently": True, "name": "ix"},
    "drop_index": {"schema_name": "shop", "name": "ix", "concurrently": True, "if_exists": True},
    "vacuum": {**T, "full": True, "analyze": True},
    "analyze": T,
    "reindex": {"target": "table", "schema_name": "shop", "name": "orders", "concurrently": True},
    "refresh_materialized_view": {"schema_name": "shop", "name": "mv", "concurrently": True},
    "create_extension": {"name": "pgcrypto", "schema_name": "shop", "if_not_exists": True},
    "grant": {
        "privileges": ["SELECT"],
        "on": "table",
        "schema_name": "shop",
        "name": "orders",
        "role": "nw_ro",
    },
}


def test_payloads_cover_every_action() -> None:
    assert set(PAYLOADS) == set(PostgresConnector.sdk_action_metas())


@pytest.mark.parametrize("action", sorted(PAYLOADS))
async def test_action_runs(action: str, conn: FakeConn) -> None:
    result = await make().run({"action": action, **PAYLOADS[action]})
    assert result.success, result.message
    assert conn.statements  # something was sent to the database


@pytest.mark.parametrize(
    "extra",
    [
        {"operation": "drop_column", "column": "c"},
        {"operation": "rename_column", "column": "c", "new_name": "d"},
        {"operation": "rename_table", "new_name": "o2"},
        {"operation": "set_type", "column": "c", "column_type": "numeric(10,2)"},
        {"operation": "set_default", "column": "c", "default": 5},
        {"operation": "drop_default", "column": "c"},
        {"operation": "set_not_null", "column": "c"},
        {"operation": "drop_not_null", "column": "c"},
        {"operation": "add_column", "column": "c", "column_type": "int", "default": 1},
    ],
)
async def test_alter_table_operations(extra: dict[str, Any], conn: FakeConn) -> None:
    assert (await make().run({"action": "alter_table", **T, **extra})).success


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "index", "schema_name": "shop", "name": "ix"},
        {"target": "schema", "schema_name": "shop"},
    ],
)
async def test_reindex_targets(payload: dict[str, Any], conn: FakeConn) -> None:
    assert (await make().run({"action": "reindex", **payload})).success


@pytest.mark.parametrize(
    "payload",
    [
        {"privileges": ["USAGE"], "on": "schema", "schema_name": "shop", "role": "r"},
        {
            "privileges": ["ALL"],
            "on": "sequence",
            "schema_name": "shop",
            "name": "s",
            "role": "r",
            "revoke": True,
        },
    ],
)
async def test_grant_variants(payload: dict[str, Any], conn: FakeConn) -> None:
    assert (await make().run({"action": "grant", **payload})).success


async def test_vacuum_and_analyze_without_table(conn: FakeConn) -> None:
    assert (await make().run({"action": "vacuum"})).success
    assert (await make().run({"action": "analyze", "schema_name": "shop"})).success


async def test_index_and_catalog_variants(conn: FakeConn) -> None:
    c = make()
    assert (
        await c.run({"action": "create_index", **T, "columns": ["id"], "method": "gin"})
    ).success
    assert (await c.run({"action": "list_functions", "kind": "procedure"})).success
    assert (await c.run({"action": "list_tables"})).success


# ---- result shaping -----------------------------------------------------------------------


async def test_rows_are_json_safe(conn: FakeConn) -> None:
    result = await make().run({"action": "query", **SQL})
    row = result.data["rows"][0]
    assert row["price"] == "1.50"
    assert row["at"].startswith("2026-01-02")
    assert row["raw"] == "\\x01"
    assert row["ids"] == [str(UUID(int=1))]
    assert row["doc"] == {"a": [1, 2]}
    assert result.data["columns"][0] == "id"


async def test_row_cap_marks_truncated(conn: FakeConn) -> None:
    conn.many = True
    result = await make(max_rows=2).run({"action": "query", **SQL})
    assert result.data["row_count"] == 2
    assert result.data["truncated"] is True
    one = await make().run({"action": "query_one", **SQL})
    assert one.data["row_count"] == 1


async def test_statement_without_rows_returns_rowcount(conn: FakeConn) -> None:
    conn.has_result = False
    result = await make().run({"action": "execute", "sql": "update shop.orders set total = 1"})
    assert result.data["row_count"] == 3
    assert result.data["rows"] == []


async def test_explain_analyze_is_rolled_back(conn: FakeConn) -> None:
    await make().run({"action": "explain", "sql": "delete from shop.orders", "analyze": True})
    assert conn.rolled_back


async def test_read_actions_use_a_read_only_transaction(conn: FakeConn) -> None:
    await make().run({"action": "query", **SQL})
    assert "SET TRANSACTION READ ONLY" in [s for s, _ in conn.statements if isinstance(s, str)]


async def test_raw_sql_is_prepared_so_the_server_refuses_extra_statements(conn: FakeConn) -> None:
    """E'..' strings can hide a ';' from the regex check; prepare=True makes the server enforce one
    statement (checked on a real server: "cannot insert multiple commands into a prepared statement")."""
    sneaky = r"update shop.orders set note = E'\'' ; commit; select 'a'"
    assert (
        policy.single_statement(sneaky) is None
    )  # the regex is fooled; the server is what stops it
    await make().run({"action": "execute", "sql": sneaky})
    assert conn.execute_kwargs[-1] == {"prepare": True}


async def test_autocommit_session_is_restored(conn: FakeConn) -> None:
    await make().run({"action": "vacuum", **T})
    assert conn.autocommit is False
    assert "RESET statement_timeout" in [s for s, _ in conn.statements if isinstance(s, str)]


async def test_failed_reset_neither_hides_the_result_nor_leaves_autocommit_on(
    conn: FakeConn,
) -> None:
    real = conn.execute

    async def execute(query: Any, params: Any = None) -> None:
        if query == "RESET statement_timeout":
            raise ConnectionError("connection dropped")
        await real(query, params)

    conn.execute = execute  # type: ignore[method-assign]
    result = await make().run({"action": "vacuum", **T})
    assert result.success, result.message
    assert conn.autocommit is False


async def test_copy_in_writes_every_row(conn: FakeConn) -> None:
    result = await make().run({"action": "copy_in", **PAYLOADS["copy_in"]})
    assert result.data["row_count"] == 3


async def test_copy_out_returns_csv_and_caps_size(
    conn: FakeConn, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = await make().run({"action": "copy_out", "sql": "select 1"})
    assert ok.data["data"] == "a,b\n1,2\n"
    assert ok.data["truncated"] is False
    monkeypatch.setattr(policy, "_COPY_OUT_MAX_BYTES", 5)
    cut = await make().run({"action": "copy_out", "sql": "select 1"})
    assert cut.data["truncated"] is True
    assert cut.data["data"] == "a,b\n"


# ---- policy: refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "action", "ok"),
    [
        ("read_only", "query", True),
        ("read_only", "insert", False),
        ("read_only", "drop_table", False),
        ("read_write", "insert", True),
        ("read_write", "drop_table", False),
        ("admin", "drop_table", True),
    ],
)
async def test_mode_gates_actions(mode: str, action: str, ok: bool, conn: FakeConn) -> None:
    result = await make(mode=mode).run({"action": action, **PAYLOADS[action]})
    assert result.success is ok
    if not ok:
        assert result.error_code == "POSTGRES_POLICY_DENIED"


async def test_default_mode_cannot_write(conn: FakeConn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_POSTGRES_MODE", raising=False)
    config = {"host": "h", "database": "d"}
    result = await PostgresConnector(secret_provider=Secrets(), config=config).run(
        {"action": "truncate", **T}
    )
    assert result.error_code == "POSTGRES_POLICY_DENIED"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "query", "sql": "select 1; drop table shop.orders"},
        {"action": "execute", "sql": "select 1; select 2"},
        {"action": "copy_out", "sql": "select 1; select 2"},
        {"action": "transaction", "statements": [{"sql": "select 1; select 2"}]},
        {
            "action": "create_table",
            "table": "t",
            "columns": [{"name": "a", "type": "int); drop table x; --"}],
        },
        {"action": "alter_table", **T, "operation": "add_column", "column": "c"},
        {"action": "alter_table", **T, "operation": "rename_table"},
        {"action": "alter_table", **T, "operation": "set_default", "column": "c"},
        {"action": "insert", **T, "rows": [{"a": 1}, {"b": 2}]},
    ],
)
async def test_unsafe_or_incomplete_input_is_refused(
    payload: dict[str, Any], conn: FakeConn
) -> None:
    result = await make().run(payload)
    assert result.success is False
    assert result.error_code == "POSTGRES_POLICY_DENIED"
    assert not any("select 1" in str(q).lower() for q, _ in conn.statements)


async def test_too_many_values_is_refused(conn: FakeConn) -> None:
    rows = [{f"c{j}": j for j in range(70)} for _ in range(1000)]
    result = await make().run({"action": "insert", **T, "rows": rows})
    assert result.error_code == "POSTGRES_POLICY_DENIED"


async def test_schema_and_table_allowlists(conn: FakeConn) -> None:
    c = make(allowed_schemas=["shop"], allowed_tables=["shop.orders"])
    assert (await c.run({"action": "count", **T})).success
    other_schema = await c.run({"action": "count", "schema_name": "hr", "table": "orders"})
    assert other_schema.error_code == "POSTGRES_POLICY_DENIED"
    other_table = await c.run({"action": "count", "schema_name": "shop", "table": "customers"})
    assert other_table.error_code == "POSTGRES_POLICY_DENIED"
    assert (
        await c.run({"action": "list_schemas"})
    ).success  # catalog reads are scoped, not refused


# ---- settings and the pool ---------------------------------------------------------------------


def test_settings_precedence_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "envhost")
    monkeypatch.setenv("POSTGRES_DATABASE", "envdb")
    monkeypatch.setenv("NW_POSTGRES_MAX_ROWS", "7")
    monkeypatch.setenv("NW_POSTGRES_ALLOWED_SCHEMAS", "a, b")
    s = policy.settings({"host": "cfghost"})
    assert (s.host, s.database, s.max_rows) == ("cfghost", "envdb", 7)
    assert set(s.schemas) == {"a", "b"}
    with pytest.raises(PostgresPolicyError, match="mode must be"):
        policy.settings({"mode": "root"})
    monkeypatch.delenv("POSTGRES_HOST")
    with pytest.raises(PostgresPolicyError, match="host and database"):
        policy.settings({})


async def test_pool_is_reused_per_credential_set(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    class FakeAsyncPool:
        def __init__(self, conninfo: str, **kw: Any) -> None:
            self.conninfo = conninfo
            opened.append(conninfo)

        async def open(self) -> None:
            return None

    monkeypatch.setattr(policy, "AsyncConnectionPool", FakeAsyncPool)
    monkeypatch.setattr(policy, "_pools", {})
    c = make()
    s = policy.settings(c.config)
    first = await policy._pool(c, s)
    assert await policy._pool(c, s) is first
    assert len(opened) == 1

    class Other(Secrets):
        def get_secret(self, key: str) -> str:
            return "other"

    c2 = PostgresConnector(secret_provider=Other(), config=CONFIG)
    assert await policy._pool(c2, policy.settings(c2.config)) is not first
    assert len(opened) == 2


# ---- errors -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code", "category"),
    [
        ("InsufficientPrivilege", "POSTGRES_PERMISSION_DENIED", "AUTH"),
        ("QueryCanceled", "POSTGRES_STATEMENT_TIMEOUT", "BUSINESS"),
        ("ReadOnlySqlTransaction", "POSTGRES_READ_ONLY", "BUSINESS"),
        ("UniqueViolation", "POSTGRES_INTEGRITY_ERROR", "BUSINESS"),
    ],
)
async def test_database_errors_are_mapped(
    exc: str, code: str, category: str, conn: FakeConn, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg.errors as pge

    async def boom(self: FakeCursor, query: Any, params: Any = None) -> None:
        raise getattr(pge, exc)("nope")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    result = await make().run({"action": "query", **SQL})
    assert (result.error_code, result.error_category) == (code, category)

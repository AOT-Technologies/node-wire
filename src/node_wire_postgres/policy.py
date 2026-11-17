#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Postgres plumbing: settings, pools, safe SQL building, and the execution guard.

Everything that is not an action lives here so ``logic.py`` holds only actions.
Host, port, database and sslmode come from connectors.yaml / env, never the payload.
Credentials come from the secret provider. The database role is the real security
boundary; the checks here are defence in depth.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, time as dtime
from typing import Any

from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .schema import ColumnDef, PostgresOutput

logger = logging.getLogger("connectors.postgres")

MODES = ("read_only", "read_write", "admin")

# Shared SQL fragments for the discovery actions (``n`` is pg_namespace).
SYSTEM_SCHEMAS = "(%(sys)s OR (n.nspname NOT LIKE 'pg\\_%%' AND n.nspname <> 'information_schema'))"
IN_SCOPE = "(%(s)s::text[] IS NULL OR n.nspname = ANY(%(s)s))"
FOR_TABLE = "(%(schema)s::text IS NULL OR n.nspname = %(schema)s)"
FOR_TABLE += " AND (%(table)s::text IS NULL OR c.relname = %(table)s)"


COLUMNS = """
SELECT n.nspname AS schema, c.relname AS "table", a.attnum AS position, a.attname AS name,
       format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS "default",
       COALESCE(a.attnum = ANY(i.indkey), false) AS primary_key,
       col_description(a.attrelid, a.attnum) AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
LEFT JOIN pg_index i ON i.indrelid = a.attrelid AND i.indisprimary
WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind::text IN ('r', 'v', 'm', 'f', 'p')
"""


class PostgresPolicyError(Exception):
    """A request the connector refuses before it reaches the database."""


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    database: str
    sslmode: str
    mode: str
    timeout_ms: int
    max_rows: int
    pool_max: int
    schemas: tuple[str, ...]
    tables: tuple[str, ...]


def _list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    return tuple(str(v).strip() for v in (value or ()) if str(v).strip())


def settings(config: dict[str, Any]) -> Settings:
    """connectors.yaml values win; env vars are the fallback. Default mode is admin."""

    def get(key: str, env: str, default: Any = None) -> Any:
        value = config.get(key)
        return value if value not in (None, "") else os.getenv(env, default)

    host = get("host", "POSTGRES_HOST")
    database = get("database", "POSTGRES_DATABASE")
    if not host or not database:
        raise PostgresPolicyError("Postgres host and database must be set (config or env)")
    mode = str(get("mode", "NW_POSTGRES_MODE", "admin"))
    if mode not in MODES:
        raise PostgresPolicyError(f"mode must be one of {', '.join(MODES)}")
    return Settings(
        host=str(host),
        port=int(get("port", "POSTGRES_PORT", 5432)),
        database=str(database),
        sslmode=str(get("sslmode", "POSTGRES_SSLMODE", "prefer")),
        mode=mode,
        timeout_ms=int(get("statement_timeout_ms", "NW_POSTGRES_STATEMENT_TIMEOUT_MS", 30000)),
        max_rows=int(get("max_rows", "NW_POSTGRES_MAX_ROWS", 1000)),
        pool_max=int(get("pool_max", "NW_POSTGRES_POOL_MAX", 5)),
        schemas=_list(get("allowed_schemas", "NW_POSTGRES_ALLOWED_SCHEMAS")),
        tables=_list(get("allowed_tables", "NW_POSTGRES_ALLOWED_TABLES")),
    )


# Simplified: pools live for the process; there is no explicit shutdown hook.
_pools: dict[str, AsyncConnectionPool] = {}
_lock = asyncio.Lock()


async def _pool(connector: Any, s: Settings) -> AsyncConnectionPool:
    user = connector.secret_provider.get_secret("POSTGRES_USERNAME")
    password = connector.secret_provider.get_secret("POSTGRES_PASSWORD")
    conninfo = make_conninfo(
        host=s.host,
        port=s.port,
        dbname=s.database,
        sslmode=s.sslmode,
        user=user,
        password=password,
        connect_timeout=10,
        application_name="node-wire",
    )
    # One pool per connection string (host, db, user, password), so tenants never share a
    # connection. The key stays in process memory, like the pool's own copy; nothing is hashed.
    key = conninfo
    async with _lock:
        pool = _pools.get(key)
        if pool is None:
            pool = AsyncConnectionPool(conninfo, min_size=0, max_size=s.pool_max, open=False)
            await pool.open()
            _pools[key] = pool
    return pool


# ---- safe SQL building ------------------------------------------------------

_QUOTED = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$(\w*)\$.*?\$\1\$|--[^\n]*|/\*.*?\*/", re.S
)
_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_. ]*(\(\s*\d+(\s*,\s*\d+)?\s*\))?(\[\])*$")


def single_statement(query: str) -> None:
    """Reject ``a; b``. The read-only transaction and the DB role still apply to the one statement."""
    if ";" in _QUOTED.sub(" ", query).strip().rstrip(";"):
        raise PostgresPolicyError("Only one statement is allowed; use the transaction action")


def column_type(value: str) -> sql.SQL:
    """Column types cannot be quoted as identifiers, so only a plain type name is accepted."""
    if not _TYPE.match(value):
        raise PostgresPolicyError(f"Unsupported column type: {value!r}")
    return sql.SQL(value)


def adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, dict) else value


def qualified(connector: Any, schema: str, name: str | None = None) -> sql.Composable:
    """``"schema"."name"`` (or just the schema), after the schema/table allowlists."""
    s = settings(connector.config)
    if s.schemas and schema not in s.schemas:
        raise PostgresPolicyError(f"Schema {schema!r} is not in the allowed schemas")
    if name is None:
        return sql.Identifier(schema)
    if s.tables and f"{schema}.{name}" not in s.tables:
        raise PostgresPolicyError(f"Table {schema}.{name} is not in the allowed tables")
    return sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(name)])


def routine(connector: Any, schema: str, name: str) -> sql.Composable:
    """``"schema"."name"`` for a function, index or view. Only the schema allowlist applies."""
    return sql.SQL("{}.{}").format(qualified(connector, schema), sql.Identifier(name))


def opt(flag: bool, text: str) -> sql.SQL:
    return sql.SQL(text if flag else "")


def need(operation: str, **fields: Any) -> None:
    missing = [k for k, v in fields.items() if v is None]
    if missing:
        raise PostgresPolicyError(f"{operation} needs: {', '.join(missing)}")


def bind(params: Any) -> Any:
    """Dict values become jsonb; a dict of named params is passed through."""
    return [adapt(v) for v in params] if isinstance(params, list) else params


def column_def(c: ColumnDef) -> sql.Composable:
    default: sql.Composable = sql.SQL("")
    if c.default is not None:
        default = sql.SQL(" DEFAULT {}").format(sql.Literal(c.default))
    return sql.SQL("{} {}{}{}{}").format(
        sql.Identifier(c.name),
        column_type(c.type),
        opt(not c.nullable, " NOT NULL"),
        opt(c.unique, " UNIQUE"),
        default,
    )


def rows_sql(rows: list[dict[str, Any]]) -> tuple[list[str], sql.Composable, list[Any]]:
    """``(cols) VALUES (...), (...)`` plus the flat parameter list for a multi-row insert."""
    cols = list(rows[0])
    if not cols or any(set(r) != set(cols) for r in rows):
        raise PostgresPolicyError("Every row must have the same, non-empty set of columns")
    if len(rows) * len(cols) > 65535:
        raise PostgresPolicyError("Too many values for one statement; split the rows")
    one = sql.SQL("({})").format(sql.SQL(", ").join(sql.Placeholder() for _ in cols))
    values = sql.SQL("({}) VALUES {}").format(names(cols), sql.SQL(", ").join([one] * len(rows)))
    return cols, values, [adapt(r[c]) for r in rows for c in cols]


def names(columns: list[str]) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(c) for c in columns)


def returning(columns: list[str] | None) -> sql.Composable:
    return sql.SQL(" RETURNING ") + names(columns) if columns else sql.SQL("")


def where(cond: dict[str, Any] | None) -> tuple[sql.Composable, list[Any]]:
    """AND-ed equality filters: ``NULL`` becomes IS NULL, a list becomes ``= ANY``."""
    parts: list[sql.Composable] = []
    values: list[Any] = []
    for column, value in (cond or {}).items():
        if value is None:
            parts.append(sql.SQL("{} IS NULL").format(sql.Identifier(column)))
        elif isinstance(value, list):
            parts.append(sql.SQL("{} = ANY(%s)").format(sql.Identifier(column)))
            values.append(value)
        else:
            parts.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            values.append(adapt(value))
    if not parts:
        return sql.SQL(""), []
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), values


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, dtime)):
        return value.isoformat()
    return str(value)  # Decimal, UUID, timedelta, ranges, ... keep exact text


# ---- execution --------------------------------------------------------------


@asynccontextmanager
async def _session(connector: Any, mode: str, autocommit: bool = False):
    """A pooled connection with timeout set; read-only is enforced by the database itself."""
    s = settings(connector.config)
    if MODES.index(mode) > MODES.index(s.mode):
        raise PostgresPolicyError(f"This action needs mode {mode!r}; the connector is {s.mode!r}")
    pool = await _pool(connector, s)
    timeout = sql.SQL("{}").format(sql.Literal(s.timeout_ms))
    async with pool.connection() as conn:
        if autocommit:  # VACUUM, CREATE INDEX CONCURRENTLY and friends refuse a transaction block
            await conn.set_autocommit(True)
            try:
                await conn.execute(sql.SQL("SET statement_timeout = ") + timeout)
                yield conn, s
            finally:
                await conn.execute("RESET statement_timeout")
                await conn.set_autocommit(False)
        else:
            if mode == "read_only" or s.mode == "read_only":
                await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute(sql.SQL("SET LOCAL statement_timeout = ") + timeout)
            yield conn, s


def _log(connector: Any, trace_id: str, mode: str, started: float, rows: int) -> None:
    # The statement text is not logged: it can carry data.
    logger.info(
        "Postgres statement finished",
        extra={
            "trace_id": trace_id,
            "connector_id": connector.connector_id,
            "mode": mode,
            "row_count": rows,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )


async def run(
    connector: Any,
    trace_id: str,
    mode: str,
    query: Any,
    params: Any = None,
    *,
    stream: bool = False,
    max_rows: int | None = None,
    rollback: bool = False,
    autocommit: bool = False,
) -> PostgresOutput:
    """Run one statement. ``stream`` uses a server-side cursor (SELECT/WITH/VALUES only) so a
    huge result is never fully loaded; every path returns at most ``max_rows`` rows."""
    if isinstance(query, str):
        single_statement(query)
    started = time.monotonic()
    async with _session(connector, mode, autocommit) as (conn, s):
        cap = min(max_rows or s.max_rows, s.max_rows)
        cursor = conn.cursor(name="nw_cursor", row_factory=dict_row) if stream else None
        async with cursor or conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params or None)
            if cur.description:
                rows = await cur.fetchmany(cap + 1)
                out = PostgresOutput(
                    columns=[d.name for d in cur.description],
                    rows=[_json(r) for r in rows[:cap]],
                    row_count=min(len(rows), cap),
                    truncated=len(rows) > cap,
                )
            else:
                out = PostgresOutput(row_count=cur.rowcount)
        if rollback:
            await conn.rollback()
    _log(connector, trace_id, mode, started, out.row_count)
    return out


async def meta(connector: Any, trace_id: str, query: str, **params: Any) -> PostgresOutput:
    """Catalog read, limited to the allowed schemas when an allowlist is configured."""
    allowed = list(settings(connector.config).schemas) or None
    values = {"sys": False, "schema": None, "table": None, "s": allowed, **params}
    return await run(connector, trace_id, "read_only", query, values)


async def run_many(
    connector: Any, trace_id: str, mode: str, statements: list[tuple[str, Any]]
) -> PostgresOutput:
    """Several statements in one transaction: all commit, or none do."""
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    async with _session(connector, mode) as (conn, _):
        async with conn.cursor() as cur:
            for i, (query, params) in enumerate(statements):
                single_statement(query)
                await cur.execute(query, params or None)
                rows.append({"statement": i, "row_count": cur.rowcount})
    _log(connector, trace_id, mode, started, len(rows))
    return PostgresOutput(columns=["statement", "row_count"], rows=rows, row_count=len(rows))


async def copy_in(
    connector: Any, trace_id: str, query: Any, data: list[list[Any]]
) -> PostgresOutput:
    started = time.monotonic()
    async with _session(connector, "read_write") as (conn, _):
        async with conn.cursor() as cur:
            async with cur.copy(query) as copy:
                for row in data:
                    await copy.write_row([adapt(v) for v in row])
            count = cur.rowcount
    _log(connector, trace_id, "read_write", started, count)
    return PostgresOutput(row_count=count)


# Simplified: COPY OUT output is capped at a fixed size instead of a config knob.
_COPY_OUT_MAX_BYTES = 10 * 1024 * 1024


async def copy_out(connector: Any, trace_id: str, query: str, params: Any = None) -> PostgresOutput:
    single_statement(query)
    copy_sql = (
        sql.SQL("COPY (")
        + sql.SQL(query.strip().rstrip(";"))
        + sql.SQL(") TO STDOUT WITH (FORMAT csv, HEADER true)")
    )
    started = time.monotonic()
    chunks: list[bytes] = []
    size = 0
    truncated = False
    async with _session(connector, "read_only") as (conn, _):
        async with conn.cursor() as cur:
            async with cur.copy(copy_sql, params) as copy:
                async for block in copy:
                    size += len(block)
                    if size > _COPY_OUT_MAX_BYTES:
                        truncated = True
                        break
                    chunks.append(bytes(block))
    _log(connector, trace_id, "read_only", started, 0)
    return PostgresOutput(data=b"".join(chunks).decode("utf-8", "replace"), truncated=truncated)

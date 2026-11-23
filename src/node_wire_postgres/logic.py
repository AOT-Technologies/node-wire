#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   PostgreSQL server: tested on 16 (postgres:16-alpine). Designed for 11 or later:
#     prokind (functions/procedures, CREATE PROCEDURE) needs 11; REINDEX ... CONCURRENTLY needs 12.
#     17 and 18 should work (stable catalog views) but are not tested. Older than 11 is unsupported.
#   Driver: psycopg 3 (>=3.2,<4, tested 3.3.9; bundles libpq 18) and psycopg-pool (>=3.2,<4).
#   Python: 3.11 or later.
from __future__ import annotations

from typing import ClassVar, Dict, Tuple, Type

import psycopg
from psycopg import errors, sql
from psycopg_pool import PoolTimeout

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.tenant_persistence import declare_secret_shape

from .policy import (
    COLUMNS,
    FOR_TABLE,
    IN_SCOPE,
    SYSTEM_SCHEMAS,
    PostgresPolicyError,
    bind,
    column_def,
    column_type,
    copy_in,
    copy_out,
    meta,
    names,
    need,
    opt,
    qualified,
    returning,
    rows_sql,
    routine,
    run,
    run_many,
    where,
    adapt,
)
from .schema import (
    ActiveSessionsInput,
    AlterTableInput,
    AnalyzeInput,
    CallFunctionInput,
    CallProcedureInput,
    CancelBackendInput,
    ColumnDef,
    CopyInInput,
    CopyOutInput,
    CountInput,
    CreateExtensionInput,
    CreateIndexInput,
    CreateSchemaInput,
    CreateTableInput,
    DeleteInput,
    DescribeTableInput,
    DropIndexInput,
    DropSchemaInput,
    DropTableInput,
    ExecuteInput,
    ExistsInput,
    ExplainInput,
    GetFunctionDefinitionInput,
    GrantInput,
    InsertInput,
    ListColumnsInput,
    ListConstraintsInput,
    ListDatabasesInput,
    ListExtensionsInput,
    ListForeignKeysInput,
    ListFunctionsInput,
    ListIndexesInput,
    ListLocksInput,
    ListPrivilegesInput,
    ListRolesInput,
    ListSchemasInput,
    ListSequencesInput,
    ListTablesInput,
    ListTriggersInput,
    ListTypesInput,
    PingInput,
    PostgresOutput,
    QueryInput,
    QueryOneInput,
    ReindexInput,
    RefreshMaterializedViewInput,
    SampleRowsInput,
    ServerVersionInput,
    ShowSettingsInput,
    TableSizeInput,
    TerminateBackendInput,
    TransactionInput,
    TruncateInput,
    UpdateInput,
    UpsertInput,
    VacuumInput,
    WhoAmIInput,
)


class PostgresConnector(BaseConnector):
    """Postgres connector: structured and raw SQL actions over a pooled psycopg 3 connection.

    Each action names the lowest mode it needs (read_only < read_write < admin); the
    connector mode defaults to read_only and can be raised in connectors.yaml.
    """

    connector_id = "postgres"
    output_model = PostgresOutput

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        PostgresPolicyError: (ErrorCategory.FATAL, "POSTGRES_POLICY_DENIED"),
        PoolTimeout: (ErrorCategory.RETRYABLE, "POSTGRES_POOL_TIMEOUT"),
        psycopg.OperationalError: (ErrorCategory.RETRYABLE, "POSTGRES_CONNECTION_ERROR"),
        errors.InvalidPassword: (ErrorCategory.AUTH, "POSTGRES_AUTH_ERROR"),
        errors.InvalidAuthorizationSpecification: (ErrorCategory.AUTH, "POSTGRES_AUTH_ERROR"),
        errors.InsufficientPrivilege: (ErrorCategory.AUTH, "POSTGRES_PERMISSION_DENIED"),
        errors.QueryCanceled: (ErrorCategory.BUSINESS, "POSTGRES_STATEMENT_TIMEOUT"),
        errors.ReadOnlySqlTransaction: (ErrorCategory.BUSINESS, "POSTGRES_READ_ONLY"),
        errors.DeadlockDetected: (ErrorCategory.RETRYABLE, "POSTGRES_DEADLOCK"),
        errors.SerializationFailure: (ErrorCategory.RETRYABLE, "POSTGRES_SERIALIZATION_FAILURE"),
        psycopg.IntegrityError: (ErrorCategory.BUSINESS, "POSTGRES_INTEGRITY_ERROR"),
        psycopg.DataError: (ErrorCategory.BUSINESS, "POSTGRES_DATA_ERROR"),
        psycopg.ProgrammingError: (ErrorCategory.BUSINESS, "POSTGRES_SQL_ERROR"),
        psycopg.Error: (ErrorCategory.FATAL, "POSTGRES_ERROR"),
    }

    # ---- server and session -------------------------------------------------

    @nw_action("ping")
    async def ping(self, params: PingInput, *, trace_id: str) -> PostgresOutput:
        return await meta(self, trace_id, "SELECT 1 AS ok")

    @nw_action("server_version")
    async def server_version(self, params: ServerVersionInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT version() AS version, current_setting('server_version_num')::int AS version_num",
        )

    @nw_action("who_am_i")
    async def who_am_i(self, params: WhoAmIInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT current_user AS current_user, session_user AS session_user, "
            "current_database() AS database, current_schema() AS schema, "
            "pg_backend_pid() AS pid",
        )

    @nw_action("list_databases")
    async def list_databases(self, params: ListDatabasesInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT datname AS name, pg_get_userbyid(datdba) AS owner, "
            "pg_encoding_to_char(encoding) AS encoding, datcollate AS collation, "
            "CASE WHEN has_database_privilege(datname, 'CONNECT') "
            "THEN pg_database_size(datname) END AS size_bytes "
            "FROM pg_database WHERE NOT datistemplate ORDER BY 1",
        )

    @nw_action("show_settings")
    async def show_settings(self, params: ShowSettingsInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT name, setting, unit, category, short_desc, context FROM pg_settings "
            "WHERE name ILIKE %(pattern)s ORDER BY name",
            pattern=params.pattern,
        )

    @nw_action("active_sessions")
    async def active_sessions(
        self, params: ActiveSessionsInput, *, trace_id: str
    ) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT pid, usename AS user, datname AS database, application_name, "
            "client_addr::text AS client_addr, state, wait_event_type, wait_event, "
            "backend_start, xact_start, query_start, left(query, 500) AS query "
            "FROM pg_stat_activity WHERE backend_type = 'client backend' "
            "AND pid <> pg_backend_pid() AND (%(idle)s OR state <> 'idle') ORDER BY query_start",
            idle=params.include_idle,
        )

    @nw_action("list_locks")
    async def list_locks(self, params: ListLocksInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT l.pid, l.locktype, l.mode, l.granted, l.relation::regclass::text AS relation, "
            "a.usename AS user, left(a.query, 500) AS query "
            "FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
            "WHERE l.pid <> pg_backend_pid() AND (NOT %(waiting)s OR NOT l.granted) ORDER BY l.pid",
            waiting=params.only_waiting,
        )

    @nw_action("cancel_backend")
    async def cancel_backend(self, params: CancelBackendInput, *, trace_id: str) -> PostgresOutput:
        return await run(
            self, trace_id, "admin", "SELECT pg_cancel_backend(%s) AS cancelled", [params.pid]
        )

    @nw_action("terminate_backend")
    async def terminate_backend(
        self, params: TerminateBackendInput, *, trace_id: str
    ) -> PostgresOutput:
        return await run(
            self, trace_id, "admin", "SELECT pg_terminate_backend(%s) AS terminated", [params.pid]
        )

    # ---- schema discovery ---------------------------------------------------

    @nw_action("list_schemas")
    async def list_schemas(self, params: ListSchemasInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT n.nspname AS name, pg_get_userbyid(n.nspowner) AS owner FROM pg_namespace n "
            f"WHERE {SYSTEM_SCHEMAS} AND {IN_SCOPE} ORDER BY 1",
            sys=params.include_system,
        )

    @nw_action("list_tables")
    async def list_tables(self, params: ListTablesInput, *, trace_id: str) -> PostgresOutput:
        relkind = {
            "table": "r",
            "view": "v",
            "materialized_view": "m",
            "foreign_table": "f",
            "partitioned_table": "p",
        }
        return await meta(
            self,
            trace_id,
            "SELECT n.nspname AS schema, c.relname AS name, CASE c.relkind "
            "WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' "
            "WHEN 'f' THEN 'foreign_table' WHEN 'p' THEN 'partitioned_table' END AS kind, "
            "pg_get_userbyid(c.relowner) AS owner, c.reltuples::bigint AS row_estimate "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind::text = ANY(%(kinds)s) AND {FOR_TABLE} AND {SYSTEM_SCHEMAS} "
            f"AND {IN_SCOPE} ORDER BY 1, 2",
            kinds=[relkind[k] for k in params.kinds],
            schema=params.schema_name,
            sys=params.include_system,
        )

    @nw_action("describe_table")
    async def describe_table(self, params: DescribeTableInput, *, trace_id: str) -> PostgresOutput:
        qualified(self, params.schema_name, params.table)
        return await meta(
            self,
            trace_id,
            f"{COLUMNS} AND {FOR_TABLE} ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("list_columns")
    async def list_columns(self, params: ListColumnsInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            f"{COLUMNS} AND {FOR_TABLE} AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
            sys=params.include_system,
        )

    @nw_action("list_indexes")
    async def list_indexes(self, params: ListIndexesInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            'SELECT n.nspname AS schema, c.relname AS "table", i.relname AS name, '
            "pg_get_indexdef(x.indexrelid) AS definition, x.indisunique AS is_unique, "
            "x.indisprimary AS is_primary FROM pg_index x "
            "JOIN pg_class c ON c.oid = x.indrelid JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {FOR_TABLE} AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("list_constraints")
    async def list_constraints(
        self, params: ListConstraintsInput, *, trace_id: str
    ) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            'SELECT n.nspname AS schema, c.relname AS "table", k.conname AS name, '
            "CASE k.contype WHEN 'p' THEN 'primary_key' WHEN 'f' THEN 'foreign_key' "
            "WHEN 'u' THEN 'unique' WHEN 'c' THEN 'check' WHEN 'x' THEN 'exclusion' END AS type, "
            "pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {FOR_TABLE} AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("list_foreign_keys")
    async def list_foreign_keys(
        self, params: ListForeignKeysInput, *, trace_id: str
    ) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            'SELECT n.nspname AS schema, c.relname AS "table", k.conname AS name, '
            "k.confrelid::regclass::text AS references, pg_get_constraintdef(k.oid) AS definition "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE k.contype = 'f' AND {FOR_TABLE} AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} "
            "ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("list_sequences")
    async def list_sequences(self, params: ListSequencesInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT n.nspname AS schema, s.relname AS name, "
            "format_type(q.seqtypid, NULL) AS data_type, q.seqstart AS start_value, "
            "q.seqmin AS min_value, q.seqmax AS max_value, q.seqincrement AS increment_by "
            "FROM pg_sequence q JOIN pg_class s ON s.oid = q.seqrelid "
            "JOIN pg_namespace n ON n.oid = s.relnamespace "
            f"WHERE (%(schema)s::text IS NULL OR n.nspname = %(schema)s) AND {SYSTEM_SCHEMAS} "
            f"AND {IN_SCOPE} ORDER BY 1, 2",
            schema=params.schema_name,
        )

    @nw_action("list_functions")
    async def list_functions(self, params: ListFunctionsInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT n.nspname AS schema, p.proname AS name, "
            "pg_get_function_identity_arguments(p.oid) AS arguments, "
            "pg_get_function_result(p.oid) AS returns, CASE p.prokind WHEN 'f' THEN 'function' "
            "WHEN 'p' THEN 'procedure' WHEN 'a' THEN 'aggregate' WHEN 'w' THEN 'window' END AS kind, "
            "l.lanname AS language FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "JOIN pg_language l ON l.oid = p.prolang "
            "WHERE (%(schema)s::text IS NULL OR n.nspname = %(schema)s) "
            "AND (%(kind)s = 'all' OR p.prokind::text = CASE %(kind)s WHEN 'function' THEN 'f' ELSE 'p' END) "
            f"AND p.prokind::text IN ('f', 'p') AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} ORDER BY 1, 2",
            schema=params.schema_name,
            kind=params.kind,
            sys=params.include_system,
        )

    @nw_action("get_function_definition")
    async def get_function_definition(
        self, params: GetFunctionDefinitionInput, *, trace_id: str
    ) -> PostgresOutput:
        qualified(self, params.schema_name)
        return await meta(
            self,
            trace_id,
            "SELECT p.oid::regprocedure::text AS signature, pg_get_functiondef(p.oid) AS definition "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = %(schema)s AND p.proname = %(name)s AND p.prokind::text IN ('f', 'p')",
            schema=params.schema_name,
            name=params.name,
        )

    @nw_action("list_triggers")
    async def list_triggers(self, params: ListTriggersInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            'SELECT n.nspname AS schema, c.relname AS "table", t.tgname AS name, '
            "pg_get_triggerdef(t.oid) AS definition, t.tgenabled::text AS enabled "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE NOT t.tgisinternal AND {FOR_TABLE} AND {SYSTEM_SCHEMAS} AND {IN_SCOPE} "
            "ORDER BY 1, 2, 3",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("list_extensions")
    async def list_extensions(
        self, params: ListExtensionsInput, *, trace_id: str
    ) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT e.extname AS name, e.extversion AS version, n.nspname AS schema, "
            "x.default_version, x.comment FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace "
            "LEFT JOIN pg_available_extensions x ON x.name = e.extname ORDER BY 1",
        )

    @nw_action("list_types")
    async def list_types(self, params: ListTypesInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT n.nspname AS schema, t.typname AS name, CASE t.typtype WHEN 'e' THEN 'enum' "
            "WHEN 'c' THEN 'composite' WHEN 'd' THEN 'domain' WHEN 'r' THEN 'range' END AS kind, "
            "(SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_enum e "
            "WHERE e.enumtypid = t.oid) AS labels FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typtype::text IN ('e', 'c', 'd', 'r') AND NOT (t.typtype::text = 'c' AND "
            "EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = t.typrelid AND c.relkind::text <> 'c')) "
            f"AND (%(schema)s::text IS NULL OR n.nspname = %(schema)s) AND {SYSTEM_SCHEMAS} "
            f"AND {IN_SCOPE} ORDER BY 1, 2",
            schema=params.schema_name,
        )

    @nw_action("list_roles")
    async def list_roles(self, params: ListRolesInput, *, trace_id: str) -> PostgresOutput:
        return await meta(
            self,
            trace_id,
            "SELECT rolname AS name, rolsuper AS superuser, rolcreatedb AS create_db, "
            "rolcreaterole AS create_role, rolcanlogin AS can_login, rolreplication AS replication, "
            "rolconnlimit AS connection_limit, rolvaliduntil AS valid_until FROM pg_roles "
            "WHERE %(sys)s OR rolname NOT LIKE 'pg\\_%%' ORDER BY 1",
            sys=params.include_system,
        )

    @nw_action("list_privileges")
    async def list_privileges(
        self, params: ListPrivilegesInput, *, trace_id: str
    ) -> PostgresOutput:
        qualified(self, params.schema_name, params.table)
        return await meta(
            self,
            trace_id,
            "SELECT grantee, privilege_type, is_grantable FROM information_schema.role_table_grants "
            "WHERE table_schema = %(schema)s AND table_name = %(table)s ORDER BY 1, 2",
            schema=params.schema_name,
            table=params.table,
        )

    @nw_action("table_size")
    async def table_size(self, params: TableSizeInput, *, trace_id: str) -> PostgresOutput:
        qualified(self, params.schema_name, params.table)
        return await meta(
            self,
            trace_id,
            "SELECT pg_total_relation_size(c.oid) AS total_bytes, pg_relation_size(c.oid) AS table_bytes, "
            "pg_indexes_size(c.oid) AS index_bytes, c.reltuples::bigint AS row_estimate FROM pg_class c "
            "WHERE c.oid = to_regclass(format('%%I.%%I', %(schema)s::text, %(table)s::text))",
            schema=params.schema_name,
            table=params.table,
        )

    # ---- reading data -------------------------------------------------------

    @nw_action("query")
    async def query(self, params: QueryInput, *, trace_id: str) -> PostgresOutput:
        return await run(
            self,
            trace_id,
            "read_only",
            params.sql,
            bind(params.params),
            stream=True,
            max_rows=params.max_rows,
        )

    @nw_action("query_one")
    async def query_one(self, params: QueryOneInput, *, trace_id: str) -> PostgresOutput:
        return await run(
            self,
            trace_id,
            "read_only",
            params.sql,
            bind(params.params),
            stream=True,
            max_rows=1,
        )

    @nw_action("count")
    async def count(self, params: CountInput, *, trace_id: str) -> PostgresOutput:
        clause, values = where(params.where)
        query = sql.SQL("SELECT count(*) AS count FROM {}{}").format(
            qualified(self, params.schema_name, params.table), clause
        )
        return await run(self, trace_id, "read_only", query, values)

    @nw_action("exists")
    async def exists(self, params: ExistsInput, *, trace_id: str) -> PostgresOutput:
        clause, values = where(params.where)
        query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {}{}) AS exists").format(
            qualified(self, params.schema_name, params.table), clause
        )
        return await run(self, trace_id, "read_only", query, values)

    @nw_action("sample_rows")
    async def sample_rows(self, params: SampleRowsInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("SELECT * FROM {} LIMIT %s").format(
            qualified(self, params.schema_name, params.table)
        )
        return await run(self, trace_id, "read_only", query, [params.limit])

    @nw_action("explain")
    async def explain(self, params: ExplainInput, *, trace_id: str) -> PostgresOutput:
        options = "FORMAT JSON, ANALYZE" if params.analyze else "FORMAT JSON"
        return await run(
            self,
            trace_id,
            "read_write" if params.analyze else "read_only",
            f"EXPLAIN ({options}) {params.sql}",
            bind(params.params),
            rollback=params.analyze,
        )

    # ---- writing data -------------------------------------------------------

    @nw_action("insert")
    async def insert(self, params: InsertInput, *, trace_id: str) -> PostgresOutput:
        _, values, args = rows_sql(params.rows)
        query = sql.SQL("INSERT INTO {} {}{}").format(
            qualified(self, params.schema_name, params.table), values, returning(params.returning)
        )
        return await run(self, trace_id, "read_write", query, args)

    @nw_action("upsert")
    async def upsert(self, params: UpsertInput, *, trace_id: str) -> PostgresOutput:
        cols, values, args = rows_sql(params.rows)
        update = (
            params.update_columns
            if params.update_columns is not None
            else [c for c in cols if c not in params.conflict_columns]
        )
        resolution: sql.Composable = sql.SQL("DO NOTHING")
        if update:
            resolution = sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in update
            )
        query = sql.SQL("INSERT INTO {} {} ON CONFLICT ({}) {}{}").format(
            qualified(self, params.schema_name, params.table),
            values,
            names(params.conflict_columns),
            resolution,
            returning(params.returning),
        )
        return await run(self, trace_id, "read_write", query, args)

    @nw_action("update")
    async def update(self, params: UpdateInput, *, trace_id: str) -> PostgresOutput:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(c)) for c in params.values
        )
        clause, values = where(params.where)
        query = sql.SQL("UPDATE {} SET {}{}{}").format(
            qualified(self, params.schema_name, params.table),
            assignments,
            clause,
            returning(params.returning),
        )
        return await run(
            self,
            trace_id,
            "read_write",
            query,
            [adapt(v) for v in params.values.values()] + values,
        )

    @nw_action("delete")
    async def delete(self, params: DeleteInput, *, trace_id: str) -> PostgresOutput:
        clause, values = where(params.where)
        query = sql.SQL("DELETE FROM {}{}{}").format(
            qualified(self, params.schema_name, params.table), clause, returning(params.returning)
        )
        return await run(self, trace_id, "read_write", query, values)

    @nw_action("execute")
    async def execute(self, params: ExecuteInput, *, trace_id: str) -> PostgresOutput:
        return await run(self, trace_id, "read_write", params.sql, bind(params.params))

    @nw_action("transaction")
    async def transaction(self, params: TransactionInput, *, trace_id: str) -> PostgresOutput:
        return await run_many(
            self,
            trace_id,
            "read_write",
            [(s.sql, bind(s.params)) for s in params.statements],
        )

    @nw_action("copy_in")
    async def copy_in(self, params: CopyInInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("COPY {} ({}) FROM STDIN").format(
            qualified(self, params.schema_name, params.table), names(params.columns)
        )
        return await copy_in(self, trace_id, query, params.rows)

    @nw_action("copy_out")
    async def copy_out(self, params: CopyOutInput, *, trace_id: str) -> PostgresOutput:
        return await copy_out(self, trace_id, params.sql, bind(params.params))

    @nw_action("call_function")
    async def call_function(self, params: CallFunctionInput, *, trace_id: str) -> PostgresOutput:
        marks = sql.SQL(", ").join(sql.Placeholder() for _ in params.args)
        query = sql.SQL("SELECT * FROM {}({})").format(
            routine(self, params.schema_name, params.name), marks
        )
        return await run(self, trace_id, "read_write", query, bind(params.args))

    @nw_action("call_procedure")
    async def call_procedure(self, params: CallProcedureInput, *, trace_id: str) -> PostgresOutput:
        marks = sql.SQL(", ").join(sql.Placeholder() for _ in params.args)
        query = sql.SQL("CALL {}({})").format(routine(self, params.schema_name, params.name), marks)
        return await run(self, trace_id, "read_write", query, bind(params.args))

    # ---- DDL ----------------------------------------------------------------

    @nw_action("create_schema")
    async def create_schema(self, params: CreateSchemaInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("CREATE SCHEMA {}{}").format(
            opt(params.if_not_exists, "IF NOT EXISTS "), qualified(self, params.name)
        )
        return await run(self, trace_id, "admin", query)

    @nw_action("drop_schema")
    async def drop_schema(self, params: DropSchemaInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("DROP SCHEMA {}{}{}").format(
            opt(params.if_exists, "IF EXISTS "),
            qualified(self, params.name),
            opt(params.cascade, " CASCADE"),
        )
        return await run(self, trace_id, "admin", query)

    @nw_action("create_table")
    async def create_table(self, params: CreateTableInput, *, trace_id: str) -> PostgresOutput:
        parts = [column_def(c) for c in params.columns]
        if params.primary_key:
            parts.append(sql.SQL("PRIMARY KEY ({})").format(names(params.primary_key)))
        query = sql.SQL("CREATE TABLE {}{} ({})").format(
            opt(params.if_not_exists, "IF NOT EXISTS "),
            qualified(self, params.schema_name, params.table),
            sql.SQL(", ").join(parts),
        )
        return await run(self, trace_id, "admin", query)

    @nw_action("alter_table")
    async def alter_table(self, params: AlterTableInput, *, trace_id: str) -> PostgresOutput:
        op = params.operation
        table = qualified(self, params.schema_name, params.table)
        column = sql.Identifier(params.column) if params.column else None
        if op == "rename_table":
            need(op, new_name=params.new_name)
            change = sql.SQL("RENAME TO {}").format(sql.Identifier(params.new_name or ""))
        else:
            need(op, column=params.column)
            if op == "add_column":
                need(op, column_type=params.column_type)
                change = sql.SQL("ADD COLUMN {}").format(
                    column_def(
                        ColumnDef(
                            name=params.column or "",
                            type=params.column_type or "",
                            nullable=params.nullable,
                            default=params.default,
                        )
                    )
                )
            elif op == "drop_column":
                change = sql.SQL("DROP COLUMN {}").format(column)
            elif op == "rename_column":
                need(op, new_name=params.new_name)
                change = sql.SQL("RENAME COLUMN {} TO {}").format(
                    column, sql.Identifier(params.new_name or "")
                )
            elif op == "set_type":
                need(op, column_type=params.column_type)
                change = sql.SQL("ALTER COLUMN {} TYPE {}").format(
                    column, column_type(params.column_type or "")
                )
            elif op == "set_default":
                need(op, default=params.default)
                change = sql.SQL("ALTER COLUMN {} SET DEFAULT {}").format(
                    column, sql.Literal(params.default)
                )
            elif op == "drop_default":
                change = sql.SQL("ALTER COLUMN {} DROP DEFAULT").format(column)
            elif op == "set_not_null":
                change = sql.SQL("ALTER COLUMN {} SET NOT NULL").format(column)
            else:
                change = sql.SQL("ALTER COLUMN {} DROP NOT NULL").format(column)
        return await run(
            self, trace_id, "admin", sql.SQL("ALTER TABLE {} {}").format(table, change)
        )

    @nw_action("drop_table")
    async def drop_table(self, params: DropTableInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("DROP TABLE {}{}{}").format(
            opt(params.if_exists, "IF EXISTS "),
            qualified(self, params.schema_name, params.table),
            opt(params.cascade, " CASCADE"),
        )
        return await run(self, trace_id, "admin", query)

    @nw_action("truncate")
    async def truncate(self, params: TruncateInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("TRUNCATE TABLE {}{}{}").format(
            qualified(self, params.schema_name, params.table),
            opt(params.restart_identity, " RESTART IDENTITY"),
            opt(params.cascade, " CASCADE"),
        )
        return await run(self, trace_id, "admin", query)

    @nw_action("create_index")
    async def create_index(self, params: CreateIndexInput, *, trace_id: str) -> PostgresOutput:
        name = params.name or f"{params.table}_{'_'.join(params.columns)}_idx"[:63]
        query = sql.SQL("CREATE {}INDEX {}{}{} ON {} USING {} ({})").format(
            opt(params.unique, "UNIQUE "),
            opt(params.concurrently, "CONCURRENTLY "),
            opt(params.if_not_exists, "IF NOT EXISTS "),
            sql.Identifier(name),
            qualified(self, params.schema_name, params.table),
            sql.SQL(params.method),
            names(params.columns),
        )
        return await run(self, trace_id, "admin", query, autocommit=params.concurrently)

    @nw_action("drop_index")
    async def drop_index(self, params: DropIndexInput, *, trace_id: str) -> PostgresOutput:
        query = sql.SQL("DROP INDEX {}{}{}").format(
            opt(params.concurrently, "CONCURRENTLY "),
            opt(params.if_exists, "IF EXISTS "),
            routine(self, params.schema_name, params.name),
        )
        return await run(self, trace_id, "admin", query, autocommit=params.concurrently)

    @nw_action("vacuum")
    async def vacuum(self, params: VacuumInput, *, trace_id: str) -> PostgresOutput:
        options = [o for o, on in (("FULL", params.full), ("ANALYZE", params.analyze)) if on]
        target: sql.Composable = sql.SQL("")
        if params.table:
            target = sql.SQL(" {}").format(
                qualified(self, params.schema_name or "public", params.table)
            )
        query = sql.SQL("VACUUM{}{}").format(
            sql.SQL(f" ({', '.join(options)})" if options else ""), target
        )
        return await run(self, trace_id, "admin", query, autocommit=True)

    @nw_action("analyze")
    async def analyze(self, params: AnalyzeInput, *, trace_id: str) -> PostgresOutput:
        target: sql.Composable = sql.SQL("")
        if params.table:
            target = sql.SQL(" {}").format(
                qualified(self, params.schema_name or "public", params.table)
            )
        return await run(self, trace_id, "admin", sql.SQL("ANALYZE{}").format(target))

    @nw_action("reindex")
    async def reindex(self, params: ReindexInput, *, trace_id: str) -> PostgresOutput:
        if params.target == "schema":
            target = qualified(self, params.schema_name)
        else:
            need("reindex", name=params.name)
            target = (
                qualified(self, params.schema_name, params.name or "")
                if params.target == "table"
                else routine(self, params.schema_name, params.name or "")
            )
        query = sql.SQL("REINDEX {}{} {}").format(
            sql.SQL(params.target.upper()), opt(params.concurrently, " CONCURRENTLY"), target
        )
        return await run(self, trace_id, "admin", query, autocommit=params.concurrently)

    @nw_action("refresh_materialized_view")
    async def refresh_materialized_view(
        self, params: RefreshMaterializedViewInput, *, trace_id: str
    ) -> PostgresOutput:
        query = sql.SQL("REFRESH MATERIALIZED VIEW{} {}{}").format(
            opt(params.concurrently, " CONCURRENTLY"),
            qualified(self, params.schema_name, params.name),
            opt(not params.with_data, " WITH NO DATA"),
        )
        return await run(self, trace_id, "read_write", query)

    @nw_action("create_extension")
    async def create_extension(
        self, params: CreateExtensionInput, *, trace_id: str
    ) -> PostgresOutput:
        query = sql.SQL("CREATE EXTENSION {}{}").format(
            opt(params.if_not_exists, "IF NOT EXISTS "), sql.Identifier(params.name)
        )
        if params.schema_name:
            query += sql.SQL(" SCHEMA {}").format(qualified(self, params.schema_name))
        return await run(self, trace_id, "admin", query)

    @nw_action("grant")
    async def grant(self, params: GrantInput, *, trace_id: str) -> PostgresOutput:
        if params.on == "schema":
            target = qualified(self, params.schema_name)
        else:
            need("grant", name=params.name)
            target = qualified(self, params.schema_name, params.name or "")
        query = sql.SQL("{} {} ON {} {} {} {}").format(
            sql.SQL("REVOKE" if params.revoke else "GRANT"),
            sql.SQL(", ").join(sql.SQL(p) for p in params.privileges),
            sql.SQL(params.on.upper()),
            target,
            sql.SQL("FROM" if params.revoke else "TO"),
            sql.Identifier(params.role),
        )
        return await run(self, trace_id, "admin", query)


# Tenant-secret contract for the config store; connection settings (host, port,
# database, sslmode) are not secrets and come from connectors.yaml or env.
declare_secret_shape(
    "postgres",
    required=["POSTGRES_USERNAME", "POSTGRES_PASSWORD"],
    formats={
        "POSTGRES_USERNAME": "opaque_secret",
        "POSTGRES_PASSWORD": "opaque_secret",  # nosec B105
    },
)

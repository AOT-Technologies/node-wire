#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _drop_titles(schema: dict[str, Any]) -> None:
    """Remove the model and field titles Pydantic generates; names already say it."""
    schema.pop("title", None)
    for prop in (schema.get("properties") or {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)


Ident = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[^\x00]+$")]
Params = Annotated[
    list[Any] | dict[str, Any] | None,
    Field(description="Bound parameters: a list for %s placeholders, a dict for %(name)s."),
]
Where = Annotated[
    dict[str, Any],
    Field(description="Column -> value, AND-ed. null means IS NULL, a list means = ANY."),
]


class _Base(BaseModel):
    # Simplified: one shared config instead of repeating it on every model.
    model_config = ConfigDict(populate_by_name=True, extra="forbid", json_schema_extra=_drop_titles)


class _Table(_Base):
    schema_name: Ident = "public"
    table: Ident


class _Scope(_Base):
    schema_name: Ident | None = None
    table: Ident | None = None


class PostgresOutput(BaseModel):
    """Every action returns rows (or an affected-row count). Cells are JSON-safe values."""

    model_config = ConfigDict(extra="allow", json_schema_extra=_drop_titles)
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    row_count: int = 0
    truncated: bool = False
    data: str | None = None


# ---- server and session -----------------------------------------------------


class PingInput(_Base):
    "Check the database is reachable"

    action: Literal["ping"] = "ping"


class ServerVersionInput(_Base):
    "Server version string and number"

    action: Literal["server_version"] = "server_version"


class WhoAmIInput(_Base):
    "Current user, database, schema and backend pid"

    action: Literal["who_am_i"] = "who_am_i"


class ListDatabasesInput(_Base):
    "List databases with owner, encoding and size"

    action: Literal["list_databases"] = "list_databases"


class ShowSettingsInput(_Base):
    "Show server settings whose name matches a pattern"

    action: Literal["show_settings"] = "show_settings"
    pattern: str = Field("%", description="ILIKE pattern on the setting name, e.g. 'work_mem%'.")


class ActiveSessionsInput(_Base):
    "List client sessions and what they are running"

    action: Literal["active_sessions"] = "active_sessions"
    include_idle: bool = False


class ListLocksInput(_Base):
    "List locks held or waited on"

    action: Literal["list_locks"] = "list_locks"
    only_waiting: bool = False


class CancelBackendInput(_Base):
    "Cancel the running query of a backend"

    action: Literal["cancel_backend"] = "cancel_backend"
    pid: int


class TerminateBackendInput(_Base):
    "Terminate a backend session"

    action: Literal["terminate_backend"] = "terminate_backend"
    pid: int


# ---- schema discovery -------------------------------------------------------


class ListSchemasInput(_Base):
    "List schemas"

    action: Literal["list_schemas"] = "list_schemas"
    include_system: bool = False


class ListTablesInput(_Base):
    "List tables, views, materialized views, foreign and partitioned tables"

    action: Literal["list_tables"] = "list_tables"
    schema_name: Ident | None = None
    kinds: list[
        Literal["table", "view", "materialized_view", "foreign_table", "partitioned_table"]
    ] = ["table", "view", "materialized_view", "foreign_table", "partitioned_table"]
    include_system: bool = False


class DescribeTableInput(_Table):
    "Describe a table: columns, types, nullability, defaults, primary key"

    action: Literal["describe_table"] = "describe_table"


class ListColumnsInput(_Scope):
    "List columns, optionally for one schema or table"

    action: Literal["list_columns"] = "list_columns"
    include_system: bool = False


class ListIndexesInput(_Scope):
    "List indexes with their definitions"

    action: Literal["list_indexes"] = "list_indexes"


class ListConstraintsInput(_Scope):
    "List primary key, unique, check, exclusion and foreign key constraints"

    action: Literal["list_constraints"] = "list_constraints"


class ListForeignKeysInput(_Scope):
    "List foreign keys and the tables they reference"

    action: Literal["list_foreign_keys"] = "list_foreign_keys"


class ListSequencesInput(_Base):
    "List sequences"

    action: Literal["list_sequences"] = "list_sequences"
    schema_name: Ident | None = None


class ListFunctionsInput(_Base):
    "List functions and procedures"

    action: Literal["list_functions"] = "list_functions"
    schema_name: Ident | None = None
    kind: Literal["all", "function", "procedure"] = "all"
    include_system: bool = False


class GetFunctionDefinitionInput(_Base):
    "Get the source of a function or procedure"

    action: Literal["get_function_definition"] = "get_function_definition"
    schema_name: Ident = "public"
    name: Ident


class ListTriggersInput(_Scope):
    "List triggers with their definitions"

    action: Literal["list_triggers"] = "list_triggers"


class ListExtensionsInput(_Base):
    "List installed extensions"

    action: Literal["list_extensions"] = "list_extensions"


class ListTypesInput(_Base):
    "List enums, composite types, domains and ranges"

    action: Literal["list_types"] = "list_types"
    schema_name: Ident | None = None


class ListRolesInput(_Base):
    "List roles and their attributes"

    action: Literal["list_roles"] = "list_roles"
    include_system: bool = False


class ListPrivilegesInput(_Table):
    "List table privileges by grantee"

    action: Literal["list_privileges"] = "list_privileges"


class TableSizeInput(_Table):
    "Table, index and total size in bytes, plus the row estimate"

    action: Literal["table_size"] = "table_size"


# ---- reading data -----------------------------------------------------------


class QueryInput(_Base):
    "Run a SELECT/WITH/VALUES query with bound parameters, a row cap and a timeout"

    action: Literal["query"] = "query"
    sql: str = Field(min_length=1, description="One statement. Use %s or %(name)s for values.")
    params: Params = None
    max_rows: int | None = Field(None, ge=1, description="Capped by the connector max_rows.")


class QueryOneInput(_Base):
    "Run a query and return at most one row"

    action: Literal["query_one"] = "query_one"
    sql: str = Field(min_length=1)
    params: Params = None


class CountInput(_Table):
    "Count rows, optionally filtered"

    action: Literal["count"] = "count"
    where: Where | None = None


class ExistsInput(_Table):
    "Check whether any row matches"

    action: Literal["exists"] = "exists"
    where: Where | None = None


class SampleRowsInput(_Table):
    "Return a few rows from a table"

    action: Literal["sample_rows"] = "sample_rows"
    limit: int = Field(10, ge=1, le=1000)


class ExplainInput(_Base):
    "Show the query plan as JSON"

    action: Literal["explain"] = "explain"
    sql: str = Field(min_length=1)
    params: Params = None
    analyze: bool = Field(
        False, description="Runs the statement for real timings, then rolls it back."
    )


# ---- writing data -----------------------------------------------------------


class InsertInput(_Table):
    "Insert one or many rows"

    action: Literal["insert"] = "insert"
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    returning: list[Ident] | None = None


class UpsertInput(_Table):
    "Insert rows, updating them on conflict"

    action: Literal["upsert"] = "upsert"
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    conflict_columns: list[Ident] = Field(min_length=1)
    update_columns: list[Ident] | None = Field(
        None, description="Columns to overwrite. Default: all non-conflict columns."
    )
    returning: list[Ident] | None = None


class UpdateInput(_Table):
    "Update rows; where is mandatory"

    action: Literal["update"] = "update"
    values: dict[str, Any] = Field(min_length=1)
    where: Where = Field(min_length=1)
    returning: list[Ident] | None = None


class DeleteInput(_Table):
    "Delete rows; where is mandatory"

    action: Literal["delete"] = "delete"
    where: Where = Field(min_length=1)
    returning: list[Ident] | None = None


class ExecuteInput(_Base):
    "Run one DML statement (INSERT/UPDATE/DELETE, optionally with RETURNING)"

    action: Literal["execute"] = "execute"
    sql: str = Field(min_length=1)
    params: Params = None


class Statement(_Base):
    sql: str = Field(min_length=1)
    params: Params = None


class TransactionInput(_Base):
    "Run several statements in one transaction: all commit or none do"

    action: Literal["transaction"] = "transaction"
    statements: list[Statement] = Field(min_length=1, max_length=100)


class CopyInInput(_Table):
    "Bulk load rows with COPY"

    action: Literal["copy_in"] = "copy_in"
    columns: list[Ident] = Field(min_length=1)
    rows: list[list[Any]] = Field(min_length=1, max_length=100_000)


class CopyOutInput(_Base):
    "Export a SELECT as CSV with COPY"

    action: Literal["copy_out"] = "copy_out"
    sql: str = Field(min_length=1, description="A SELECT; no trailing semicolon.")
    params: Params = None


class CallFunctionInput(_Base):
    "Call a function and return its rows"

    action: Literal["call_function"] = "call_function"
    schema_name: Ident = "public"
    name: Ident
    args: list[Any] = []


class CallProcedureInput(_Base):
    "Call a procedure"

    action: Literal["call_procedure"] = "call_procedure"
    schema_name: Ident = "public"
    name: Ident
    args: list[Any] = []


# ---- DDL --------------------------------------------------------------------


class ColumnDef(_Base):
    name: Ident
    type: str = Field(description="Plain type name, e.g. text, numeric(10,2), timestamptz, int[].")
    nullable: bool = True
    unique: bool = False
    default: str | int | float | bool | None = Field(None, description="A literal value.")


class CreateSchemaInput(_Base):
    "Create a schema"

    action: Literal["create_schema"] = "create_schema"
    name: Ident
    if_not_exists: bool = True


class DropSchemaInput(_Base):
    "Drop a schema"

    action: Literal["drop_schema"] = "drop_schema"
    name: Ident
    cascade: bool = False
    if_exists: bool = True


class CreateTableInput(_Table):
    "Create a table"

    action: Literal["create_table"] = "create_table"
    columns: list[ColumnDef] = Field(min_length=1)
    primary_key: list[Ident] | None = None
    if_not_exists: bool = True


class AlterTableInput(_Table):
    "Alter a table: add/drop/rename a column, change type, nullability or default, rename the table"

    action: Literal["alter_table"] = "alter_table"
    operation: Literal[
        "add_column",
        "drop_column",
        "rename_column",
        "rename_table",
        "set_type",
        "set_not_null",
        "drop_not_null",
        "set_default",
        "drop_default",
    ]
    column: Ident | None = None
    new_name: Ident | None = None
    column_type: str | None = None
    nullable: bool = True
    default: str | int | float | bool | None = None


class DropTableInput(_Table):
    "Drop a table"

    action: Literal["drop_table"] = "drop_table"
    cascade: bool = False
    if_exists: bool = True


class TruncateInput(_Table):
    "Remove all rows from a table"

    action: Literal["truncate"] = "truncate"
    restart_identity: bool = False
    cascade: bool = False


class CreateIndexInput(_Table):
    "Create an index"

    action: Literal["create_index"] = "create_index"
    columns: list[Ident] = Field(min_length=1)
    name: Ident | None = Field(None, description="Default: <table>_<columns>_idx.")
    unique: bool = False
    method: Literal["btree", "hash", "gist", "gin", "brin", "spgist"] = "btree"
    concurrently: bool = False
    if_not_exists: bool = True


class DropIndexInput(_Base):
    "Drop an index"

    action: Literal["drop_index"] = "drop_index"
    schema_name: Ident = "public"
    name: Ident
    concurrently: bool = False
    if_exists: bool = True


class VacuumInput(_Scope):
    "Vacuum a table, or the whole database when no table is given"

    action: Literal["vacuum"] = "vacuum"
    full: bool = False
    analyze: bool = False


class AnalyzeInput(_Scope):
    "Update planner statistics for a table, or the whole database"

    action: Literal["analyze"] = "analyze"


class ReindexInput(_Base):
    "Rebuild indexes"

    action: Literal["reindex"] = "reindex"
    target: Literal["table", "index", "schema"]
    schema_name: Ident = "public"
    name: Ident | None = Field(None, description="Table or index name; unused for a schema.")
    concurrently: bool = False


class RefreshMaterializedViewInput(_Base):
    "Refresh a materialized view"

    action: Literal["refresh_materialized_view"] = "refresh_materialized_view"
    schema_name: Ident = "public"
    name: Ident
    concurrently: bool = False
    with_data: bool = True


class CreateExtensionInput(_Base):
    "Install an extension"

    action: Literal["create_extension"] = "create_extension"
    name: Ident
    schema_name: Ident | None = None
    if_not_exists: bool = True


class GrantInput(_Base):
    "Grant (or revoke) privileges on a table, schema or sequence"

    action: Literal["grant"] = "grant"
    privileges: list[
        Literal[
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
            "USAGE",
            "CREATE",
            "ALL",
        ]
    ] = Field(min_length=1)
    on: Literal["table", "schema", "sequence"]
    schema_name: Ident = "public"
    name: Ident | None = Field(None, description="Table or sequence name; unused for a schema.")
    role: Ident
    revoke: bool = False

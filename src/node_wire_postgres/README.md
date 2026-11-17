<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# postgres connector

> **Connector ID:** `postgres`
> **Driver:** psycopg 3 with `psycopg_pool`
> **Source:** `src/node_wire_postgres/`

Hand-written. `logic.py` holds only the actions, `schema.py` the models, and `policy.py` the pool,
safe-SQL building and the read-only/limit guards.

## Configuration

| Setting | `connectors.yaml` key | Env | Default |
|---|---|---|---|
| Host | `host` | `POSTGRES_HOST` | required |
| Port | `port` | `POSTGRES_PORT` | `5432` |
| Database | `database` | `POSTGRES_DATABASE` | required |
| SSL mode | `sslmode` | `POSTGRES_SSLMODE` | `prefer` |
| Mode | `mode` | `NW_POSTGRES_MODE` | `admin` |
| Statement timeout | `statement_timeout_ms` | `NW_POSTGRES_STATEMENT_TIMEOUT_MS` | `30000` |
| Row cap | `max_rows` | `NW_POSTGRES_MAX_ROWS` | `1000` |
| Pool size | `pool_max` | `NW_POSTGRES_POOL_MAX` | `5` |
| Schema allowlist | `allowed_schemas` | `NW_POSTGRES_ALLOWED_SCHEMAS` | any |
| Table allowlist | `allowed_tables` (`schema.table`) | `NW_POSTGRES_ALLOWED_TABLES` | any |

Secrets: `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`. Connection settings are never taken from the payload.

Modes: `read_only` < `read_write` < `admin`. Actions in the Reading data and discovery groups run inside
`SET TRANSACTION READ ONLY`, so the database enforces it. Raw SQL takes exactly one statement with bound
parameters (`%s` or `%(name)s`). The database role is the real security boundary: grant it only what it needs.

## Actions


### Server and session

| Action | Description |
|---|---|
| `ping` | Check the database is reachable |
| `server_version` | Server version string and number |
| `who_am_i` | Current user, database, schema and backend pid |
| `list_databases` | List databases with owner, encoding and size |
| `show_settings` | Show server settings whose name matches a pattern |
| `active_sessions` | List client sessions and what they are running |
| `list_locks` | List locks held or waited on |
| `cancel_backend` | Cancel the running query of a backend |
| `terminate_backend` | Terminate a backend session |

### Schema discovery

| Action | Description |
|---|---|
| `list_schemas` | List schemas |
| `list_tables` | List tables, views, materialized views, foreign and partitioned tables |
| `describe_table` | Describe a table: columns, types, nullability, defaults, primary key |
| `list_columns` | List columns, optionally for one schema or table |
| `list_indexes` | List indexes with their definitions |
| `list_constraints` | List primary key, unique, check, exclusion and foreign key constraints |
| `list_foreign_keys` | List foreign keys and the tables they reference |
| `list_sequences` | List sequences |
| `list_functions` | List functions and procedures |
| `get_function_definition` | Get the source of a function or procedure |
| `list_triggers` | List triggers with their definitions |
| `list_extensions` | List installed extensions |
| `list_types` | List enums, composite types, domains and ranges |
| `list_roles` | List roles and their attributes |
| `list_privileges` | List table privileges by grantee |
| `table_size` | Table, index and total size in bytes, plus the row estimate |

### Reading data

| Action | Description |
|---|---|
| `query` | Run a SELECT/WITH/VALUES query with bound parameters, a row cap and a timeout |
| `query_one` | Run a query and return at most one row |
| `count` | Count rows, optionally filtered |
| `exists` | Check whether any row matches |
| `sample_rows` | Return a few rows from a table |
| `explain` | Show the query plan as JSON |

### Writing data

| Action | Description |
|---|---|
| `insert` | Insert one or many rows |
| `upsert` | Insert rows, updating them on conflict |
| `update` | Update rows; where is mandatory |
| `delete` | Delete rows; where is mandatory |
| `execute` | Run one DML statement (INSERT/UPDATE/DELETE, optionally with RETURNING) |
| `transaction` | Run several statements in one transaction: all commit or none do |
| `copy_in` | Bulk load rows with COPY |
| `copy_out` | Export a SELECT as CSV with COPY |
| `call_function` | Call a function and return its rows |
| `call_procedure` | Call a procedure |

### DDL

| Action | Description |
|---|---|
| `create_schema` | Create a schema |
| `drop_schema` | Drop a schema |
| `create_table` | Create a table |
| `alter_table` | Alter a table: add/drop/rename a column, change type, nullability or default, rename the table |
| `drop_table` | Drop a table |
| `truncate` | Remove all rows from a table |
| `create_index` | Create an index |
| `drop_index` | Drop an index |
| `vacuum` | Vacuum a table, or the whole database when no table is given |
| `analyze` | Update planner statistics for a table, or the whole database |
| `reindex` | Rebuild indexes |
| `refresh_materialized_view` | Refresh a materialized view |
| `create_extension` | Install an extension |
| `grant` | Grant (or revoke) privileges on a table, schema or sequence |

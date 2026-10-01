<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Grafana Quick Guide

## What is included

- `docker-compose.yml` runs `grafana/otel-lgtm` (Grafana + Loki + OTLP endpoints).
- Dashboard `connector-logs-status.json`, loaded at startup into the **node-wire** folder
  (`provisioning/dashboards/node-wire.yaml`). Edits made in the UI last until the container is
  recreated; to keep one, export it over `connector-logs-status.json`.
- Exposed ports:
  - `3000` -> Grafana UI
  - `4317` -> OTLP gRPC ingest
  - `4318` -> OTLP HTTP ingest

## Run with Docker

From the `grafana` folder:

```bash
docker compose up -d
```

If another collector already holds a port, pick others (the defaults are 3000, 4317 and 4318):

```bash
NW_OTLP_GRPC_PORT=14317 NW_OTLP_HTTP_PORT=14318 docker compose up -d
```

Stop it:

```bash
docker compose down
```

## Open Grafana

1. Open `http://localhost:3000` (no login).
2. **Dashboards** -> **node-wire** -> **Connector Calls & Logs**.

## Which services it shows

The **Service** picker lists every `service_name` Loki has seen, one per process that exports
logs:

- `node-wire`: the REST API / MCP entrypoints;
- `nw-<connector_id>`: MCP hosts built by `nw gen-all` / `nw gen-mcp` (e.g. `nw-pet_store`);
- `<server>-mcp`: stacklok-built servers (e.g. `petstore-mcp`, `slack-post-mcp`).

A server exports only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set in its environment. From a
container, the collector is at `http://host.docker.internal:4318` (or the `NW_OTLP_HTTP_PORT`
you chose), not `localhost`.

## Add the ToolHive e2e servers

`scripts/e2e_toolhive.py` deploys the MCP servers of the ToolHive runbook and, with
`--otlp-endpoint`, points them at this stack:

```bash
uv run python scripts/e2e_toolhive.py --otlp-endpoint http://host.docker.internal:4318
uv run python scripts/e2e_toolhive.py --skip-build --otlp-endpoint ...   # images already built
```

Their logs appear under `nw-pet_store`, `nw-slack_web`, `petstore-mcp` and `slack-post-mcp`, and
the script checks that each failed call's `trace_id` leads to its log line. To follow one
failure in **Explore** (Loki): `{service_name="slack-post-mcp"} | error_code="VALIDATION_ERROR"`.
`trace_id`, `error_code`, `error_category`, `audit_event`, `connector_id` and `action` are all
filterable fields.

For a steady stream of real calls (successes, upstream 404s, rejected calls), run the traffic
script against the Petstore MCP host. It calls the public Petstore demo, read-only:

```bash
uv run python scripts/traffic_petstore.py --otlp-endpoint http://host.docker.internal:4318
uv run python scripts/traffic_petstore.py --duration 600 --rate 1 --otlp-endpoint ...
```

It shows up as Service `nw-pet_store`, and ends with the calls by tool and outcome to compare
with the dashboard.

This stack keeps no volume: recreating the container (`docker compose up` after a change to this
folder, or `down`) empties Loki. Rerun the script with `--skip-build` to fill it again.

## The dashboard

Filters: **Service**, **Connector** and **Action** (from the metrics), plus **trace_id** and
**Search logs** boxes for the log panel. Paste the `trace_id` from a tool error
(`CODE [CATEGORY]: message (trace_id=...)`) to see that call's log lines.

| Row | Panels | Source |
|-----|--------|--------|
| Overview | tool calls, success rate, calls rejected before the connector ran, p95 latency | Loki `audit_event` (counts); Prometheus `connector_duration_ms` (latency) |
| Traffic | calls per minute (ok, failed by error category, rejected before running); p50 / p95 latency | Loki; Prometheus |
| Errors | failed calls by error code, category and where they failed; failures by category | Loki `error_code`, `error_category`, `audit_event` |
| Per tool | runs by outcome per service, connector and action; p95 latency per action | Loki; Prometheus |
| Logs | the log stream, newest first; expand a line for its fields | Loki |

Counts come from each run's audit log line (`audit_event` from `runtime.base_connector`), so
they are exact even for a handful of calls; Prometheus's `increase()` misses a new counter's
first sample and extrapolates, which reads wrong at low volume. Calls rejected before the
connector ran (invalid arguments, no tenant, unknown config) are their `invocation_rejected` /
`invocation_validation_failure` lines from other loggers. Latency comes from the
`connector_duration_ms` histogram, exported about once a minute; logs arrive within seconds.

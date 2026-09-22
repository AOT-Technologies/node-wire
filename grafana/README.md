<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Grafana Quick Guide

## What is included

- `docker-compose.yml` runs `grafana/otel-lgtm` (Grafana + Loki + OTLP endpoints).
- Sample dashboard: import `connector-logs-status.json`.
- Exposed ports:
  - `3000` -> Grafana UI
  - `4317` -> OTLP gRPC ingest
  - `4318` -> OTLP HTTP ingest

## Run with Docker

From the `grafana` folder:

```bash
docker compose up -d
```

Stop it:

```bash
docker compose down
```

## Open Grafana

1. Open `http://localhost:3000`.
2. If Grafana asks for a datasource during import, choose `Loki` (UID is usually `loki` in this stack).

## Import or build a dashboard

1. In Grafana, go to **Dashboards** -> **New** -> **Import**.
2. Upload `grafana/connector-logs-status.json` (or paste its contents).
3. Choose **Loki** as the datasource (UID is usually `loki` in this stack).
4. Save the dashboard.

Queries use `{service_name="node-wire"} | logfmt` with two branches: lines that already have `connector_id`, and OTEL lines that have `observed_timestamp` but an empty `connector_id` (those are also filtered by Connector Type against the message text). **All** is `.*`.

## Monitor the dashboard

- Set a useful time range (for example, last 30 minutes).
- Keep auto-refresh on (dashboard default is `30s`).
- Use **Connector Type** filter to switch between connectors (for example `fhir_epic` or `google_drive`).
- Watch panel trends while your connector traffic is running.

## Dashboard features

- **Connector filter**: `Connector Type` variable supports single/multi-select and `All`.
- **Log search**: use the logs panel search (or `Ctrl+F`) to find specific messages quickly.
- **Sort logs**: log stream is shown in descending time order (newest first).
- **Log details**: expand a log line to inspect parsed fields/labels.
- **Live refresh**: dashboard refreshes automatically every `30s`.
- **Status insights**: quick success/error visibility from stat + donut panels.

## Panels included

- `Success Rate` (Stat): percentage of successful connector runs.
- `Success vs Error Rate` (Donut): success count vs error count.
- `All Connector Logs` (Logs): live/searchable connector logs.

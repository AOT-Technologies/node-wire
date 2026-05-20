<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Grafana Quick Guide

## What is included

- `docker-compose.yml` runs `grafana/otel-lgtm` (Grafana + Loki + OTLP endpoints).
- `Connector Logs & Status - Updated-1773917850709.json` is the dashboard export you can import.
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

## Import the dashboard JSON

1. In Grafana, go to **Dashboards** -> **Import**.
2. Upload `Connector Logs & Status - Updated-1773917850709.json`.
3. Map the datasource to **Loki** if prompted.
4. Save the dashboard.

## Monitor the dashboard

- Set a useful time range (for example, last 30 minutes).
- Keep auto-refresh on (dashboard default is `30s`).
- Use **Connector Type** filter to switch between `fhir` and `google_drive`.
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

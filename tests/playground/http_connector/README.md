<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# HTTP Connector Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the HTTP connector (IT Ops) panel, click the run button with the
pre-filled defaults, and assert on the rendered pipeline state. No mocking —
every test makes a real HTTP POST via the `http_generic` connector.

## What is tested

| Test | Action |
|------|--------|
| `test_http_connector_submit_incident_default` | IT incident report — pre-filled High severity Gateway Proxy incident, all 4 steps must succeed |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/report-incident")`
call routes to the real backend, which formats an ITSM payload and dispatches it
via `http_generic` to `https://httpbin.org/post` — a public echo endpoint. No
`page.route()` interception.

The form is pre-filled in the HTML with a sample incident (title, description,
severity, component, reporter) — no field changes or dropdown selections are
needed before clicking run.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all HTTP connector tests
uv run pytest tests/playground/http_connector/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/http_connector/ --no-cov -v -s
```

> **Note:** HTTP connector tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

No connector credentials are needed — `http_generic` dispatches to the public
`httpbin.org` endpoint.

| Variable | Description |
|----------|-------------|
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## CI / GitHub Actions

HTTP connector tests run **only on manual `workflow_dispatch`** trigger
(`Actions → CI – Pytest → Run workflow`).

No secrets are required for this connector beyond the standard auth bypass flag.

## Test data and cleanup

All requests are sent to `https://httpbin.org/post`, which echoes the payload
and discards it. No records are persisted anywhere. No cleanup is required after
the session.

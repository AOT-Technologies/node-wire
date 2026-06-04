<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Cerner Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Cerner connector panel, click the run button with the
pre-filled defaults, and assert on the rendered pipeline state. No mocking
— every test hits the real Cerner FHIR R4 Sandbox API.

## What is tested

| Test | Action |
|------|--------|
| `test_cerner_post_consultation_default` | Post-consultation sync — pre-filled patient Nancy Smart, all 4 steps must succeed |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's
`fetch("/scenarios/cerner-post-consultation")` call routes to the real backend,
which authenticates via private-key JWT and calls the real Cerner FHIR R4
Sandbox. No `page.route()` interception.

The form is pre-filled in the HTML with a sandbox patient (`12724066`) and
encounter (`97957281`) — no field changes or dropdown selections are needed
before clicking run.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all Cerner tests
uv run pytest tests/playground/cerner/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/cerner/ --no-cov -v -s
```

> **Note:** Cerner tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `CERNER_CLIENT_ID` | Cerner backend application client ID |
| `CERNER_PRIVATE_KEY` | RSA private key (PEM) used for private-key JWT auth |
| `CERNER_TOKEN_URL` | Cerner token endpoint URL |
| `CERNER_KID` | Key ID (`kid`) that matches the public key registered in Cerner |
| `CERNER_FHIR_BASE_URL` | Base FHIR R4 URL including tenant ID (defaults to the Cerner code sandbox if unset) |
| `CERNER_SCOPES` | Space-separated OAuth2 scopes (optional; defaults defined in `connectors.yaml`) |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## CI / GitHub Actions

Cerner tests run **only on manual `workflow_dispatch`** trigger
(`Actions → CI – Pytest → Run workflow`).

Credentials are read from repository secrets:

| Secret | Maps to env var |
|--------|----------------|
| `CERNER_CLIENT_ID` | `CERNER_CLIENT_ID` |
| `CERNER_PRIVATE_KEY` | `CERNER_PRIVATE_KEY` |
| `CERNER_TOKEN_URL` | `CERNER_TOKEN_URL` |
| `CERNER_KID` | `CERNER_KID` |
| `CERNER_FHIR_BASE_URL` | `CERNER_FHIR_BASE_URL` |

Set these under **Settings → Secrets and variables → Actions** before triggering
the workflow.

## Test data and cleanup

The test uses the Cerner open Sandbox patient and encounter IDs pre-filled in the
Playground HTML. These are read-only sandbox resources — no records are created
or modified in a real Cerner environment. No cleanup is required after the session.

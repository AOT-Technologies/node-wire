<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Epic FHIR Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Epic FHIR (EHR) connector panel, click the run button with
the pre-filled defaults, and assert on the rendered pipeline state. No mocking
— every test hits the real Epic FHIR Sandbox API.

## What is tested

| Test | Action |
|------|--------|
| `test_epic_fhir_post_consultation_default` | Post-consultation sync — pre-filled patient Jason Smith, all 4 steps must succeed |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/post-consultation")`
call routes to the real backend, which authenticates via private-key JWT and calls
the real Epic FHIR Sandbox. No `page.route()` interception.

The form is pre-filled in the HTML with a sandbox patient (`e63wRTbPfr1p8UW81d8Seiw3`)
and encounter (`ecgXt3jVqNNpsXnNXZ3KljA3`) — no field changes or dropdown
selections are needed before clicking run.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all Epic FHIR tests
uv run pytest tests/playground/epic_fhir/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/epic_fhir/ --no-cov -v -s
```

> **Note:** Epic FHIR tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `EPIC_CLIENT_ID` | Epic backend application client ID |
| `EPIC_PRIVATE_KEY` | RSA private key (PEM) used for private-key JWT auth |
| `EPIC_TOKEN_URL` | Epic token endpoint URL |
| `EPIC_KID` | Key ID (`kid`) that matches the public key registered in Epic |
| `EPIC_FHIR_BASE_URL` | Base FHIR R4 URL (defaults to the Epic Sandbox if unset) |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## CI / GitHub Actions

Epic FHIR tests run **only on manual `workflow_dispatch`** trigger
(`Actions → CI – Pytest → Run workflow`).

Credentials are read from repository secrets:

| Secret | Maps to env var |
|--------|----------------|
| `EPIC_CLIENT_ID` | `EPIC_CLIENT_ID` |
| `EPIC_PRIVATE_KEY` | `EPIC_PRIVATE_KEY` |
| `EPIC_TOKEN_URL` | `EPIC_TOKEN_URL` |
| `EPIC_KID` | `EPIC_KID` |
| `EPIC_FHIR_BASE_URL` | `EPIC_FHIR_BASE_URL` |

Set these under **Settings → Secrets and variables → Actions** before triggering
the workflow.

## Test data and cleanup

The test uses the Epic open Sandbox patient and encounter IDs pre-filled in the
Playground HTML. These are read-only sandbox resources — no records are created
or modified in a real Epic environment. No cleanup is required after the session.

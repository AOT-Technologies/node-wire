<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Salesforce CRM Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Salesforce CRM connector panel, and assert on the rendered
pipeline state. No mocking — every test hits the real Salesforce API.

## What is tested

| Test | Action |
|------|--------|
| `test_sf_create_lead_minimal` | `create_lead` — required fields only (LastName + Company) |
| `test_sf_create_lead_full` | `create_lead` — with first name and email |
| `test_sf_create_contact_minimal` | `create_contact` — required field only (LastName) |
| `test_sf_create_contact_with_email` | `create_contact` — with first name and email |
| `test_sf_read_lead` | `read_lead` — valid Lead ID, asserts success state |
| `test_sf_read_lead_invalid_id` | `read_lead` — nonexistent ID, expects error state |
| `test_sf_read_contact` | `read_contact` — valid Contact ID, asserts success state |
| `test_sf_read_contact_invalid_id` | `read_contact` — nonexistent ID, expects error state |
| `test_sf_update_lead` | `update_lead` — rename + company change |
| `test_sf_update_lead_email` | `update_lead` — email-only update |
| `test_sf_update_contact` | `update_contact` — name update |
| `test_sf_update_contact_email` | `update_contact` — email-only update |
| `test_sf_delete_lead` | `delete_lead` — delete a freshly created Lead |
| `test_sf_delete_contact` | `delete_contact` — delete a freshly created Contact |
| `test_sf_switch_create_lead_to_read` | Cross-action switch on same page |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/salesforce-*")`
calls route to the real backend, which calls the real Salesforce API via OAuth2
refresh token. No `page.route()` interception.

Session fixtures create Lead and Contact records once via the REST API for use
across read and update tests. Delete tests each create their own fresh record.
All generated names and emails use random suffixes (e.g. `Lead839201`,
`test748203@mailinator.com`) so repeated runs never collide on duplicate values.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all Salesforce tests
uv run pytest tests/playground/salesforce/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/salesforce/ --no-cov -v -s

# Run a single test
uv run pytest tests/playground/salesforce/ --no-cov -v -k test_sf_create_lead_minimal
```

> **Note:** Salesforce tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `SALESFORCE_INSTANCE_URL` | Your Salesforce org URL, e.g. `https://orgname.my.salesforce.com` |
| `SALESFORCE_TOKEN_URL` | OAuth2 token endpoint, e.g. `https://login.salesforce.com/services/oauth2/token` |
| `SALESFORCE_CLIENT_ID` | Connected App client ID |
| `SALESFORCE_CLIENT_SECRET` | Connected App client secret |
| `SALESFORCE_REFRESH_TOKEN` | Long-lived OAuth2 refresh token |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## CI / GitHub Actions

Salesforce tests run **only on manual `workflow_dispatch`** trigger
(`Actions → CI – Pytest → Run workflow`).

Credentials are read from repository secrets:

| Secret | Maps to env var |
|--------|----------------|
| `SALESFORCE_INSTANCE_URL` | `SALESFORCE_INSTANCE_URL` |
| `SALESFORCE_TOKEN_URL` | `SALESFORCE_TOKEN_URL` |
| `SALESFORCE_CLIENT_ID` | `SALESFORCE_CLIENT_ID` |
| `SALESFORCE_CLIENT_SECRET` | `SALESFORCE_CLIENT_SECRET` |
| `SALESFORCE_REFRESH_TOKEN` | `SALESFORCE_REFRESH_TOKEN` |

Set these under **Settings → Secrets and variables → Actions** before triggering
the workflow.

## Test data and cleanup

The `real_sf_lead_id` and `real_sf_contact_id` session fixtures create one Lead
and one Contact in Salesforce at the start of the test session. These records are
**not automatically deleted** after the tests finish — clean them up manually via
the Salesforce UI or Developer Console if needed. Look for records with names
matching the pattern `IntegLead<digits>` and `IntegContact<digits>`.

The `deletable_lead_id` and `deletable_contact_id` fixtures create a fresh record
per delete test and those records are consumed (deleted) by the test itself.

Update tests mutate the session-scoped records in place (name, email). Because
Salesforce does not enforce unique constraints on Lead/Contact names, this is safe
to run multiple times without conflicts.

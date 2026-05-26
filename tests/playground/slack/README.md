<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Slack Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Slack connector panel, and assert on the rendered pipeline
state. No mocking — every test hits the real Slack API.

## What is tested

| Test | Action |
|------|--------|
| `test_slack_post_message_default` | `post_message` — default message to test channel |
| `test_slack_post_message_custom_message` | `post_message` — custom message content |
| `test_slack_post_message_invalid_channel` | `post_message` — nonexistent channel, expects error at step-1 |
| `test_slack_send_direct_message` | `send_direct_message` — DM to real user (requires `SLACK_TEST_USER_ID`) |
| `test_slack_upload_file` | `upload_file` — attach and upload a temp file |
| `test_slack_upload_remove_and_reattach` | `upload_file` — remove attachment UI, re-attach |
| `test_slack_switch_post_message_then_upload` | Cross-action switch on same page |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/slack-messaging")`
calls route to the real backend, which calls the real Slack API.
No `page.route()` interception.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all Slack tests
uv run pytest tests/playground/slack/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/slack/ --no-cov -v -s
```

> **Note:** Slack tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## Optional environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SLACK_TEST_CHANNEL` | Target channel for post and upload tests | `#general` |
| `SLACK_TEST_USER_ID` | Slack user ID (`U...`) for DM tests | *(skipped if absent)* |

The bot must be a member of `SLACK_TEST_CHANNEL`.
`test_slack_send_direct_message` is automatically skipped when `SLACK_TEST_USER_ID` is absent.

## CI / GitHub Actions

Slack tests run **only on manual `workflow_dispatch`** trigger alongside the other
playground integration tests.

Credentials are read from repository secrets:

| Secret | Maps to env var |
|--------|----------------|
| `SLACK_BOT_TOKEN` | `SLACK_BOT_TOKEN` |
| `SLACK_TEST_CHANNEL` | `SLACK_TEST_CHANNEL` |
| `SLACK_TEST_USER_ID` | `SLACK_TEST_USER_ID` |

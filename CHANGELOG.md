<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **MCP tenant selection on streamable-http is per caller.** `nw_select_config` and
  `nw_select_tenant` are kept per caller session (principal, request tenant, `Mcp-Session-Id`)
  instead of once per process, so one client's selection no longer changes another's. On HTTP,
  `nw_select_tenant` for a tenant other than the request's tenant fails with
  `TENANT_IDENTITY_MISMATCH` and lists no configs. stdio keeps one process-wide selection.
  Changing tenant drops a selected config the new tenant lacks; a selected tenant that has since
  been deleted fails with `TENANT_NOT_ALLOWED`. See `docs/architecture/tenancy.md`.
- **Generated nw-mcp-builder hosts run with MCP auth on by default.** Local-dev mode (auth off,
  scope policy `allow`) is now opt-in with `NW_MCP_LOCAL_DEV=true`, which the generated
  `.env.example` sets; detecting a container no longer decides auth. Regenerate existing hosts.
- stacklok servers keep `nw_select_config` per MCP session and tenant; a selection dropped from
  the bounded map fails once with the new `SELECTION_EXPIRED` code instead of silently using the
  tenant default.
- `OverlaySecretProvider.replace_all` takes a required `namespace=` and replaces only the keys it
  wrote under that namespace; all overlay reads and writes are locked.
- MCP client: `insufficient_scope` on a 401 or 403 re-authorizes with the new scopes; an expired
  MCP session (404) is re-initialized and the request retried once; calls after `aclose()` raise
  `McpSessionClosedError`; a callback with an unknown or expired `state` raises
  `McpOAuthSecurityError`.
- ToolHiveAgent: `run()` and `run_events()` share one loop (`AgentRunResult.outcome`); tool errors
  passed to the LLM no longer contain raw exception text, and tool results are no longer logged.
  `MultiMcpClient` sends `nw_select_*` to every server.
- `nw-cli` / builders: `gen-mcp`, `docker-build` and `gen-all --no-wheel` use source-hash wheel
  freshness (a regenerated connector's old wheel is rebuilt, never bundled), and hosts receive the
  exact wheels that were built. Generated projects are written to a temporary directory and swapped
  in only when complete; the output name comes from the fixture's `server.name`. `nw-connector-builder`
  returns a typed build outcome and recovers an interrupted promote. `gen-stacklok` reuses a saved
  scope only when the spec hash matches. stacklok generation validates the scope, fails on a
  missing template patch anchor, and defaults to locking.
- **`node-wire-runtime` 1.2.0: writes whose outcome is unknown are no longer retried.** A
  `RETRYABLE` failure after a `POST` or `PATCH` may have reached the upstream (a timeout, a dropped
  connection, a 5xx), so the call is now reported as the new `OUTCOME_UNKNOWN` code (`FATAL`)
  instead of being retried. Connect errors and `429`s are still retried for every method, and so
  is a write sent with an `Idempotency-Key` or `If-None-Exist` header or an action declared
  `@nw_action(..., idempotent=True)`. Generated REST connectors get this by HTTP method without
  regeneration; regenerated ones require runtime 1.2.0. See `docs/errors.md#retries`.
- A nested `call_action` on the same connector instance leaves the circuit breaker to its outer
  call: repeated nested upstream failures now open the breaker, and a half-open trial whose nested
  call failed (or never reached the upstream) no longer closes it. The outer call reports the
  nested call's code and category. An unmapped `FATAL` error no longer resets the breaker's
  failure count or closes a half-open trial.
- `smtp`: send failures raise typed errors instead of `ValueError`: `SMTP_AUTH_ERROR` (`AUTH`),
  `SMTP_CONNECT_ERROR` (`RETRYABLE`), `SMTP_TIMEOUT` and the new `SMTP_DISCONNECTED` (`FATAL`, not
  retried, because the message may have been sent).
- `stripe`: `create_subscription` sends one idempotency key per step (`<key>:pm`, `<key>:attach`,
  `<key>:sub`), and returns the created subscription with `partial: true` when only the follow-up
  client-secret lookup fails. `charge` reports Stripe's own status (`pending` is no longer
  reported as `failed`).
- `slack`: `upload_file` retries a failed upload or completion step on its own instead of starting
  a new upload; a completion that still fails is `SLACK_UPLOAD_ERROR` naming the file id.
- `google_drive`: `files.delete` reports `"status": "trashed"` (it moves the file to the trash).
  The cached Drive client is built off the event loop, and dropped after an `AUTH` error or when
  the auth provider's credentials change.
- `salesforce`: `update_*` actions are declared idempotent (retried after a timeout or 5xx);
  `create_*` returns `OUTCOME_UNKNOWN` instead.

### Added

- `nw wire <id>`: the idempotent wiring step (`connectors.yaml`, `sample.env`, `ALL_PACKAGES`)
  on its own; `gen-all` rerun hints are now computed from what is on disk.
- `fhir_epic` / `fhir_cerner`: searches return `next_link`, the Bundle's next-page URL.
  `fhir_epic` `create_document_reference` sends `If-None-Exist` for the first identifier with a
  system and value (FHIR conditional create), so a timed-out create is retried safely.

### Fixed

- A default config set with `set_default` (for example via REST) is kept across a restart; the
  config store enforces exactly one default per scope.
- Loading tenants no longer wipes stored MCP OAuth tokens from the secret overlay.
- A cached connector instance is rebuilt when its config record changes, including the default
  instance, which is now bounded by the cache size.
- MCP client OAuth: an expired token is refreshed after a restart (the client registration is
  stored with the tokens) instead of forcing a browser login; a token with the wrong audience is
  never stored; pending authorizations are keyed by `state` with a TTL; discovery is cached.
- The loopback redirect listener no longer closes on a browser preconnect or malformed request.
- `OAuth2AuthProvider`: tokens shorter-lived than the expiry buffer are reused, and a rejected
  refresh-token override is cleared.
- REST: the lazy connector factory is published only after it has loaded. gRPC: a dead background
  loop fails calls immediately, and health reports `SERVING` only after the server has started.
- `init_observability` is serialised by a lock, and a failure after it started configuring
  providers no longer leads a second call to install duplicate handlers and exporters.
- `BufferedStreamIterator` yields buffered items before re-raising an upstream error, and logs a
  cancelled stream as cancelled rather than completed.

## [1.1.2] - 2026-11-06

### Added

- **`node-wire-github`** connector package, generated from GitHub's REST OpenAPI spec with
  `nw-connector-builder` (1,239 actions). It is built by `scripts/build-packages.sh` and configured in
  `config/connectors.yaml` and `sample.env`.
- The playground adds a card and a generic form for every REST-exposed connector that has no
  hand-coded panel, listed by `GET /playground/connectors`. A connector built with `nw gen-all`
  appears after a server restart. In multitenancy mode the tenant header, config name and Add
  config dialog work for these connectors (`ConnectorFactory.config_template()`).
- The playground action picker is a searchable dropdown showing each action's HTTP method, and the
  GitHub token field lets you pick a personal access token or an OAuth token.

### Fixed

- `nw-connector-builder` no longer emits a redundant `pass` in generated schema models, which
  raised CodeQL `py/unnecessary-pass` alerts.
- `pyasn1` upgraded to 0.6.5 and GitHub Actions upgraded to resolve Dependabot security alerts.

## [1.1.1] - 2026-10-28

### Fixed

- Code-scanning alerts in the connector builder, stacklok builder, runtime error and REST modules,
  and the ToolHive request handler.
- Dependency lockfiles and GitHub Actions upgraded to resolve Dependabot security alerts.

## [1.1.0] - 2026-10-22

### Added

- **`nw` CLI** (`nw-cli`): one command for the OpenAPI → connector → wheel → MCP server → Docker
  pipeline (`nw gen-all`), with standalone `gen-whl`, `gen-mcp` and `docker-build` stages. See
  `docs/nw-cli.md`.
- **`nw-connector-builder`**: generates a connector package from an OpenAPI 3.0–3.2 or Swagger 2.0
  spec, with flat tool arguments, the spec's descriptions, schema constraints enforced on input, and
  a promotion gate for review. Operations the builder can't handle are dropped and reported instead
  of failing the build. Field-by-field support is in `docs/cli/openapi-coverage.md`.
- **`nw-mcp-builder`**: generates a standalone MCP server project from a connector.
- **`nw gen-stacklok`**: builds a [stacklok mcp-builder](https://github.com/stacklok/mcp-builder)
  server from an `mcp-scope.yaml` on the node-wire runtime, with tenants routed by ToolHive
  `MCPRemoteProxy`. See `docs/stacklok-mcp-servers.md`.
- **Multi-tenancy** across REST, gRPC, MCP and the playground: per-tenant configuration and secrets,
  tenant selection, and credential isolation for every connector.
- **MCP tool-search mode** (`NW_MCP_TOOL_MODE=search`): replaces the full `tools/list` with
  `nw_search_tools` and `nw_call_tool` for large connectors.
- **`node-wire-runtime` 1.1.0**: `headers:` in `connectors.yaml` for fixed request headers, refresh
  token rotation for `oauth2`, `envelope_ok_field` for APIs that answer `200 OK` with
  `{"ok": false}`, `apikey_query` auth, OpenTelemetry metrics and audit events, and
  `host_logging` for MCP hosts.
- **`node-wire-connectors`** meta-package: installs the runtime and every public connector.
- Playground LLM provider switching (OpenAI, Groq, Anthropic, Gemini), a Grafana **Connector Calls &
  Logs** dashboard, a REST `/ready` endpoint, and a published documentation site.

### Changed

- **Every package is a pure-Python `py3-none-any` wheel** instead of Cython-compiled per-platform
  wheels, and Python 3.11–3.13 is supported across the repository
  ([ADR 0003](docs/adr/0003-python-3-11-to-3-13.md)).
- **One error taxonomy on every surface** (`docs/errors.md`). MCP failures are `isError: true` with
  a code, REST refusals return the error envelope, and a missing secret is `SECRET_NOT_FOUND` /
  `TENANT_SECRET_NOT_FOUND`. **Clients matching the old codes must update.**
- **REST wire encoding**: booleans are sent as `true` / `false`, form values as JSON, and text/XML
  bodies as text. Generated packages require `node-wire-runtime>=1.1.0`.
- **Circuit breaker** counts only upstream-health failures, once per invocation, and thresholds are
  configurable per connector (`NW_BREAKER_<CONNECTOR_ID>_FAIL_MAX` / `_RESET_TIMEOUT`).
- The generic HTTP connector publishes as `node-wire-http`.

### Security

- **The playground is opt-in.** **Upgrade note:** set `NW_REST_PLAYGROUND_ENABLED=true` (and
  `NW_REST_AUTH_DISABLED=true`) to use it locally.
- **The `/v1` config and secret admin API requires scopes** (`nw:config:read`, `nw:config:write`,
  `nw:secrets:write`, `nw:platform:admin`).
- **MCP streamable-HTTP is stateless** and resolves identity, tenant and upstream bearer per
  request; `GET /mcp` returns `405`.
- **Config documents can't send process secrets to a caller-chosen host.** **Upgrade note:** add
  overriding hosts to `NW_REST_ALLOWED_HOSTS`.
- **`NW_MCP_SCOPE_POLICY_DEFAULT` accepts only `allow` or `deny`**; any other value fails startup
  instead of silently becoming `allow`.
- Generated Dockerfiles use a digest-pinned base image, a non-root user and no secrets in the image.

### Fixed

- `ErrorMapper` no longer leaks one connector's error code into another connector's errors.
- A race on the MCP HTTP transport no longer lets concurrent requests read another tenant's session
  or configuration.
- `grpcio-tools` moved to the optional `grpc-codegen` extra, fixing Windows installs without a
  matching wheel.

## [1.0.0] - 2026-06-27

First stable release. The public API is now **frozen under Semantic Versioning** —
see [docs/versioning.md](docs/versioning.md) for the stability and deprecation
policy and [docs/public-api.md](docs/public-api.md) for the supported surface.

### Added

- Versioning, stability, and deprecation policy (`docs/versioning.md`).
- Public API reference enumerating the frozen surface (`docs/public-api.md`).
- `node_wire_runtime.__version__`.
- DCO sign-off enforcement, Dependabot, weekly secret scanning, and `SUPPORT` /
  `GOVERNANCE` docs.
- Automated GitHub Release workflow with version and changelog validation, SBOM
  generation, release manifest generation, and GitHub release artifact upload.
- Tag-based PyPI publish workflow with release prerequisite checks, wheel checksum
  artifacts, and Sigstore attestations.
- CI badges in `README.md` and cross-platform pytest coverage on Linux, macOS,
  and Windows for Python 3.11 and 3.12.
- Test-coverage gate (`fail_under`) and updated security-audit install guidance
  for monorepo connector packages.

### Changed

- Promoted from Beta to **Production/Stable**; all nine packages versioned `1.0.0`.
- Connectors now require `node-wire-runtime>=1.0.0`.
- REST API authentication is now scoped to `/connectors/*` only. The playground UI
  (`/playground/*`), scenario API (`/scenarios/*`), and OpenAPI docs (`/docs`,
  `/redoc`, `/openapi.json`) are publicly accessible without credentials, making
  demo and discovery workflows viable when auth is enabled.
- Release and packaging documentation now use the `1.0.0` release flow and
  versioned MCP image examples.

### Fixed

- Connector authentication misconfiguration (OAuth2, Google service account, SMTP) now reports
  clear, actionable errors naming the setting to fix instead of library exceptions.

## [0.1.0] - 2026-06-26

### Added

- Initial public release of the Node Wire platform: runtime, connectors, and bindings.
- Nine publishable Python packages: runtime plus eight connectors (HTTP generic, Google Drive, SMTP, Stripe, Epic FHIR, Cerner FHIR, Salesforce, Slack).
- REST, gRPC, and MCP entrypoints with authentication, scope policy, and observability hooks.
- Per-connector MCP Docker images and unified MCP server (`agents.mcp_entrypoint`).
- ToolHive agent scenario documentation and sample agent workflow.
- CI quality gates: Ruff, Mypy, pytest, Bandit, pip-audit, and REUSE compliance.
- Governance docs: contributing guide, security policy, code of conduct, privacy notes, and HIPAA considerations.

### Fixed

- gRPC protobuf stubs committed and importable for production startup.
- REST API no longer requires the optional `playground` package at import time.
- Dependency lockfile upgraded to resolve known CVEs in transitive packages.
- Packaging, publish workflow, and security scanning aligned on the nine-package surface.

[1.1.2]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.1.2
[1.1.1]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.1.1
[1.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.1.0
[1.0.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.0.0
[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`node-wire-connectors`**: `pip install node-wire-connectors` installs the runtime and every public connector. A subset is the connector packages by name (`pip install node-wire-smtp node-wire-google-drive`). The meta package has no extras.
- **`nw gen-stacklok`**: builds a [stacklok mcp-builder](https://github.com/stacklok/mcp-builder)
  server from a stacklok `mcp-scope.yaml` on the node-wire runtime. It runs three stages:
  1. connector codegen from `spec.source`;
  2. wheels for the MCP image (cp313 musllinux for stacklok's Alpine base, read from the image;
     packages with unchanged sources are reused, not recompiled);
  3. the vendored stacklok generator.

  Tools keep the scope's names and descriptions and run through the node-wire connector. Auth
  follows ToolHive: the forwarded bearer credential is relayed into the connector's auth placement
  on every call. Tenants come from `X-Tenant-ID`, set by one ToolHive `MCPRemoteProxy` per tenant.
  The manifests are a backend, a NetworkPolicy, a per-tenant proxy template, and stacklok's auth
  manifests. Swagger 2.0 specs, and JSON-Schema forms OpenAPI 3.0 rejects (`type: [x, null]`,
  tuple `items`, as in Slack's spec), are converted for stacklok's parser. See
  `docs/stacklok-mcp-servers.md` and the runbook `docs/nw-cli-runbook.md`.
- **stacklok Phases 1–3 in one `nw gen-stacklok --path` run**:
  - It prepares a local strict OpenAPI 3.0 copy of the spec. The connector is still built from the
    original (`x-nw-source`).
  - It opens Claude Code with stacklok's `/ai-scoping` skill, which keeps its own questions and
    approval gates as in stacklok's flow; `--workflow`, `--auth-hint` and `--scoping-notes` seed its
    answers. `--headless`, or no terminal, runs it unattended with `claude -p`: the gates take the
    AI's recommendation, recorded in `scoping-summary.md`.
  - It then **pauses for the human review** (continue, or stop and print the resume command) and
    generates.
  - `--rescope` redoes Phase 1 (otherwise the saved scope is reused), and the connector is always
    rebuilt from the spec. An existing output project is checked before any build: you're asked in a
    terminal, and `--force` replaces it. The progress bars clear while Claude Code or a prompt has
    the terminal, and only the running stage shows a spinner.
  - The skill, its `spec-analyzer` / `endpoint-scoper` agents, and stacklok's `mcp-builder
    analyze` / `validate` CLI are vendored.
  - `scripts/install-stacklok-skills.sh` links them into `.claude/` or `.gemini/`.
  - `mcp-builder-schema` replaces `task generate-schema`.
- **`nw-stacklok-builder`**: stacklok/mcp-builder @ `7c03c38` and stacklok/mcp-template-py @
  `2bd79dc`, vendored (Apache-2.0). Only the files generation needs are included. Five vendored
  files gain hook calls into `nw_stacklok` (`UPSTREAM.md`). A scope without `runtime:` still
  generates exactly like upstream.
- **`node-wire-toolhive`** (`packages/toolhive`, not published), the optional layer for these
  servers:
  - `NodeWireClient` (tenant from the header, per-session named configs, runtime execution);
  - `RelayAuthProvider` with its opt-in hook (`NW_UPSTREAM_BEARER_CONNECTORS`);
  - `nw_list_configs` / `nw_select_config`.
- **`node-wire-bindings`**: `ConnectorFactory(auth_provider_hook=...)`, an optional hook consulted
  for every auth block (default and named schemes). With no hook, behaviour is unchanged.
- **`scripts/build-packages.sh --cibw-linux`**: Linux wheels via cibuildwheel (Docker) for any
  `CIBW_BUILD` selector, replacing only matching wheels in `dist/`. `--musllinux` is shorthand for
  `cp313-musllinux_*`. The arch is set with `NW_WHEEL_ARCHS`. These builds compile at `-O1 -g0`
  (overridable with `NW_WHEEL_CFLAGS`): about 4× faster on large generated connectors, and
  roughly half the wheel size.
  cibuildwheel 4.2.1 is now a dev dependency, so it works under `uv run`. The script also falls back
  to a `cibuildwheel` on `PATH`, then `uvx`.
- **`scripts/verify_stacklok_server.py`** and the opt-in workflow `stacklok-e2e.yml`: a generated
  server is called the way a ToolHive tenant proxy would call it.

- **`nw-cli`**: `nw gen-all` and `nw gen-mcp` take `--tool-search` / `--full-tool-list` /
  `--max-tool-listing-kb` and pass the host's tool mode to nw-mcp-builder. `gen-all` decides
  right after codegen, before the wheel builds; over budget on a terminal it pauses the progress
  bars and asks with a Rich prompt (the explanation and options come from nw-mcp-builder).
  Without a terminal: full list plus a warning. Using both mode flags is an error.

- **`nw-mcp-builder`**: tool-listing size check. Before building wheels, the connector's MCP
  tool listing is measured with the bindings' listing code. Over the budget (25 KB default,
  `--max-tool-listing-kb`) the build asks — on a terminal, with descriptive options and an
  explanation of where tool search can fail — whether to generate the full tool list or use
  tool search; without a terminal it generates the full list and warns. It never fails the
  build. `--tool-search` / `--full-tool-list` choose up front. The choice is baked into the
  generated host as its default `NW_MCP_TOOL_MODE` (`__main__.py` and Dockerfile `ENV`, still
  overridable at runtime) and documented in its README. Slack's generated connector measures
  77.2 KB for 174 tools. nw-mcp-builder now depends on `node-wire-bindings>=1.1.0` and
  `node-wire-runtime>=1.1.0` (path dependencies), which it already imported or now measures with.

- **`node-wire-bindings`** 1.1.0: opt-in **tool-search mode** for the MCP server —
  `McpServer(tool_mode="search")` or `NW_MCP_TOOL_MODE=search` (default `list`, unchanged).
  `tools/list` returns `nw_search_tools` (BM25 over tool names, descriptions and arguments;
  results carry full input schemas) and `nw_call_tool` (validates against the tool's schema,
  then runs it) instead of every tool, so the listing stays ~1–2 KB however large the connector.
  Scope policy applies to both; hidden tools are neither found nor callable; direct calls by
  name keep working. Modelled on FastMCP's search transform. On Slack's 174-tool connector the
  right tool is in the top 5 for 13 of 15 sample requests; misses are wording mismatches ("DM
  someone" vs `conversations_open`).
- **`node-wire-bindings`**: `advertised_tool`, `advertised_tools_for_connectors` and
  `tool_listing_bytes` in `bindings.mcp_server.server` — the one definition of an advertised
  tool, and its size as the MCP SDK serializes it, usable without a running server (for build
  tooling's listing-size check).

- **`nw-mcp-builder` / MCP Docker images**: generated hosts are **wheels-only** — install
  `node-wire-runtime`, new `node-wire-bindings` (`packages/bindings`), and the connector
  wheel. The previous `vendor/node_wire_src` + `PYTHONPATH=/nw_src` dual layout is gone.
  Use `nw gen-whl --bindings` (or `nw gen-whl --runtime --bindings`) to build the bindings
  wheel; `nw gen-all` builds runtime + bindings + connector.
- **`node_wire_runtime.policies`**: added package `__init__.py` so Cython wheels include the
  nested `policies` package (was previously missing / misplaced in the wheel).
- **`node-wire-runtime`**: `OAuth2AuthProvider` accepts an optional `on_refresh_token_rotated`
  callback (sync or async) for `grant_method="refresh_token"`, invoked when the IdP returns a
  refresh token that differs from the one just used, so a host app can persist the replacement.
  A rotated value is cached in memory and used for the rest of the process's lifetime
  regardless of whether a callback is configured or succeeds, so a single process keeps working
  through rotation either way; without a callback (or if it fails), only a restart before the
  new value is durably saved will break the connector. `src/bindings/factory.py` now wires this
  automatically for every YAML-configured `oauth2`/`refresh_token` connector (Salesforce
  included), persisting into the existing process-wide secret overlay, scoped per tenant/config
  the same way the rest of that connector's secrets already are.
- **`node-wire-runtime`**: form-encoded request bodies now render values as JSON rather than
  Python text. Booleans were sent as `True`/`False` instead of `true`/`false`, nested
  objects/arrays as a single-quoted Python repr instead of JSON (Slack rejects a `blocks` field
  encoded that way with `invalid_arguments`), and `None` as the literal string `"None"` instead of
  being omitted. Applies to `application/x-www-form-urlencoded` and multipart form fields.
- **`node-wire-runtime`**: `RestConnector.execute_rest` accepts `envelope_ok_field=` (default
  off). APIs that answer `200 OK` with `{"ok": false, "error": ...}` instead of an HTTP error
  status now raise `RestEnvelopeError` rather than returning a response that reads as successful.
- **`nw-connector-builder`**: generated connectors detect that convention from the spec — when an
  operation's success schema declares `ok` as a required boolean, codegen passes
  `envelope_ok_field="ok"` and maps `RestEnvelopeError` to a `BUSINESS` failure
  (`API_ENVELOPE_ERROR`). Optional or non-boolean `ok` fields are ignored, so specs without the
  convention generate unchanged code.
- **`node-wire-runtime`**: `StaticTokenAuthProvider` accepts `cache=` (default `True`). When
  `auth.host_supplied: true` is set in `connectors.yaml`, the factory builds the provider with
  `cache=False` so the secret is re-read per call and a rotated value is seen without an explicit
  `refresh()`. This surfaces rotations for live-resolving secret providers (env, overlay); AWS /
  GCP / Vault providers snapshot their bundle at init and still need recreating, and Azure Key
  Vault resolves live but costs a blocking round-trip per request.
- Connector logs now carry a structured ``connector_id`` on nested FHIR lines and
  agent tool-call lines (via a run-scoped logging filter and MCP tool-name
  mapping). Import ``grafana/connector-logs-status.json`` for the existing Loki
  dashboard (``connector_id`` plus the OTEL ``observed_timestamp`` branch).
- Playground **Add local model** for Ollama: discover tags from a running local
  server, persist custom `ollama/<model>` entries, and send `llm_base_url` with
  agent chat. `LLM_PROVIDER=ollama` is supported via the OpenAI-compatible
  client (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY`).
- Playground LLM switcher: OpenRouter via the existing OpenAI-compatible client
  (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, optional `OPENROUTER_MODELS`).
  Default catalog: `poolside/laguna-s-2.1:free`, `liquid/lfm-2.5-2.6b:free`,
  `nex-agi/nex-n2.5-pro:free`, `inclusionai/ling-3.0-flash-fin:free`.
  Free-tier OpenRouter endpoints log prompts — do not send confidential or
  personal data. The playground picker groups OpenRouter models behind one
  row that expands on hover or click.

- **`nw` CLI**:
  - One error handler for every command. A failure is reported once, in the summary panel, with
    the stage, the reason, the last 15 lines of its output and a fix hint. `--debug` (or
    `NW_DEBUG=1`) adds the traceback. Ctrl-C stops the running build tool and exits 130.
  - Build tool output stays under the progress bars unless `-v` / `--verbose` is set, or there is
    no terminal. Every run writes a full log to `<tmp>/nw-logs/`.
  - Commands print the next steps to run (`uv run nw docker-build …`, the stacklok run and
    ToolHive steps) below the summary.
  - `gen-all` reuses wheels whose sources are unchanged (a `.nw-source-<mode>.sha256` stamp per
    package); `--rebuild-wheels` rebuilds them all.
  - `docker-build --connector-id` finds the `gen-stacklok` servers built on the connector as well
    as its MCP host, with a menu when there are several. `--project <dir>` builds one by path.
  - The `gen-stacklok` Phase 2 review is an arrow-key menu: generate, show details, edit the
    scope and review again, redo the AI scoping with feedback, or stop.
- **`grafana/`**: the dashboard is rebuilt as **Connector Calls & Logs** and provisioned at
  startup, in a node-wire folder, instead of being imported by hand.
  - Calls, success rate, latency and a per-action table come from the connector metrics. The old
    rate was log lines matching `failed|error` against all log lines, which read about 97% when no
    call had succeeded.
  - An errors row groups failures by `error_code`, `error_category` and where they failed.
  - Calls rejected before the connector ran are counted from their audit log lines.
  - A log panel is filtered by `trace_id`.
  - Service, connector and action pickers replace the hard-coded `service_name="node-wire"` and
    connector list, so MCP hosts (`nw-<connector>`) and stacklok servers (`<server>-mcp`) show.
- **`grafana/docker-compose.yml`**: the host ports are overridable (`NW_GRAFANA_PORT`,
  `NW_OTLP_GRPC_PORT`, `NW_OTLP_HTTP_PORT`; defaults 3000, 4317 and 4318), for machines where
  another collector already holds them.
- **`scripts/traffic_petstore.py`** (local only): deploys the Petstore MCP host behind ToolHive
  (`pet-store-nw-mcp-traffic`) and sends it a weighted stream of tool calls against the public
  Petstore demo:
  - successes (find by status and tags, inventory, a pet that exists);
  - upstream 404s (`HTTP_STATUS_ERROR`);
  - calls rejected before any upstream request (`VALIDATION_ERROR`, `UNKNOWN_TOOL`).

  It calls read-only tools only, and a test keeps it that way. With `--otlp-endpoint` its logs,
  traces and metrics fill the `grafana/` dashboard. It ends with the calls by tool and outcome.
- **`scripts/e2e_toolhive.py`** (local only): builds the ToolHive runbook's MCP servers. That's ten
  scenarios: Petstore and Slack each on a node-wire host (full list, tool search, tenants +
  configs) and on stacklok (single tenant, tenants + configs). It builds them
  with the `nw` CLI in parallel lanes. Shared wheels are built once, and unchanged packages are
  reused. It deploys each server behind ToolHive under the runbook's workload names, then checks
  the tool listing (`thv mcp list tools`) and the error taxonomy with calls that fail before any
  upstream request. `PET_STORE_API_KEY`, `PET_STORE_ACCESS_TOKEN` and `SLACK_WEB_ACCESS_TOKEN`
  are optional: when set, they're passed to the servers and no upstream action is called.
  `--skip-build` rechecks the built images; `--clean` removes what it started.
  `--otlp-endpoint` sends the servers' logs and traces to the `grafana/` stack. It then checks, in
  Loki through Grafana, that each failed call's `trace_id` leads to its log line with the same
  `error_code`. It adds a posting-focused stacklok scope for Slack,
  `tests/nw_stacklok_builder/fixtures/stacklok/slack_post.yaml` (`post_message`,
  `list_conversations`, `auth_test`).
- **`scripts/reset_connector.py`**: removes everything `gen-all` / `gen-mcp` / `gen-stacklok`
  generated for one or more connectors (sources, packages and wheels, output projects, the saved
  scope, uncommitted wiring), so the next run starts from scratch. It refuses hand-written or
  git-tracked connectors. `--dry-run` lists what it would remove.
- **`node-wire-runtime`** 1.1.0: `node_wire_runtime.host_logging`. `configure_host_logging` prints
  the runtime's `extra` fields (`trace_id`, `error_code`, `error_category`, `audit_event` …) on
  the console, installs redaction, and starts OpenTelemetry export only when an OTLP endpoint is
  set. `install_redaction_and_telemetry` does the same for hosts that own their console format.
  nw-mcp-builder hosts call `configure_host_logging` at startup.
- **`node-wire-toolhive`** 1.1.0: `init_telemetry(service_name)`, which stacklok-built servers
  call at startup for the same redaction and opt-in OTLP export.

### Changed

- **One error taxonomy on every surface** (`docs/errors.md`). The runtime now owns every code,
  including failures before a connector runs, and bindings only translate them. Codes shipped in
  1.0.0 keep their name and category.
  - `node_wire_runtime`: `ErrorCode`, `NodeWireError` (a `ValueError`), and
    `errors.reject()` / `validation_error()` / `error_text()` / `http_status()`. Every refused
    call gets a trace id and one `invocation_rejected` / `invocation_validation_failure` log line.
  - **Node-wire MCP host**: a failed call is `isError: true`. Its text is
    `CODE [CATEGORY]: message (trace_id=…)` (validation messages keep their `Input validation
    error:` wording after the code), and the envelope is its structured content. Unknown tools,
    bad arguments, unknown tenants and configs, rate limits and a connector not on this server
    were plain text with no code: now `UNKNOWN_TOOL`, `VALIDATION_ERROR`, `TENANT_NOT_ALLOWED`,
    `CONFIG_NOT_FOUND`, `RATE_LIMIT_EXCEEDED`, `CONNECTOR_NOT_AVAILABLE`. A connector failure was
    a result with `success: false` and `isError: false`.
  - **REST**: a call refused before running keeps its status and `detail`, and its body is now the
    envelope (it was `{"detail": …}` only).
  - **gRPC**: same codes as before (`INVALID_PAYLOAD` stays gRPC's name for `VALIDATION_ERROR`
    through 1.x); refused calls now carry a trace id.
  - **stacklok-built servers** (unreleased): `TENANT_REQUIRED` is now `MISSING_TENANT`,
    `CONNECTOR_NOT_EXPOSED [FATAL]` is now `CONNECTOR_NOT_AVAILABLE [BUSINESS]`, and
    `CONFIG_NOT_FOUND` is `AUTH`, as gRPC shipped it. Unknown tools are `UNKNOWN_TOOL`.
    `node_wire_toolhive.report_argument_errors` is renamed `report_call_errors`; regenerate
    servers with `nw gen-stacklok --force`.
  - The runtime's tenant session raises coded errors (`TENANT_NOT_ALLOWED`, `TENANT_PIN_LOCKED`,
    `CONFIG_NOT_FOUND`, `MISSING_TENANT`) instead of bare `ValueError`s.
- **Error codes for a missing credential**: these used to get `ErrorMapper`'s fallback, the
  exception's class name with category `FATAL`. They now have stable codes. Clients that matched
  the old codes must update.
  - A secret the server is configured to read but lacks: `SECRET_NOT_FOUND`, or
    `TENANT_SECRET_NOT_FOUND` for a tenant's secret (was `SecretNotFoundError` /
    `TenantSecretNotFoundError`). Still `FATAL`: it's a server misconfiguration, so REST keeps
    answering 500.
  - A stacklok-built server that got no bearer token from ToolHive: `UPSTREAM_TOKEN_MISSING
    [AUTH]` (was `MissingUpstreamTokenError [FATAL]`). The caller's credential is missing.
- **Development and tooling move to Python 3.13**: the repo root, `nw-cli`,
  `nw-connector-builder`, `nw-mcp-builder` and `nw-stacklok-builder` require `>=3.13`, CI runs on
  3.13, and container images and the generated MCP host Dockerfile use `python:3.13-slim`
  (digest-pinned). Branch protection must require `Run pytest (ubuntu-latest, Python 3.13)` in
  place of the 3.11/3.12 checks. The published packages (runtime, bindings, toolhive, connectors,
  and connectors generated by `nw-connector-builder`) still declare `>=3.11`; the publish matrix
  adds `cp313` wheels (which the stacklok Alpine image installs) next to `cp311` and `cp312`.
  A `Published packages (Python 3.11/3.12)` CI job runs the runtime, bindings and toolhive tests
  on those versions, and the `Pytest matrix complete` gate requires it.
- **`nw gen-stacklok`**: `--scoping-model` defaults to Claude Code's own model (was `haiku`).
- **`nw` CLI**: fix and retry hints read `uv run nw …`, like the next steps.

- **`nw-connector-builder`**: generated tools now carry the spec's descriptions. The operation's
  `summary` (else `description`) — first paragraph, markdown links flattened, 300-character cap —
  becomes the input model's docstring, which the manifest already publishes as the tool
  description. Parameter descriptions (from the parameter or its schema) are emitted on each
  field, first sentence, 90-character cap. Previously every generated tool reached MCP clients
  with no description at all. Slack: 174/174 tools and 198/223 parameters now described. With the
  flattened bodies below, Slack's advertised input schemas go from 68 KB (no descriptions) to 79 KB.
- **`node-wire-bindings`**: MCP tool-call validation errors now name the offending path — e.g.
  `Input validation error: $.body: 'channel' is a required property` instead of
  `'channel' is a required property`. The SDK validates against `inputSchema` and reports only
  `exc.message`, dropping the `json_path` it already computed, so a message could not say whether
  a field was missing from the arguments or from an object nested inside them. One error is
  reported per call (jsonschema's best match). Validation now runs in the server
  (`validate_input=False` on the SDK handler) against the same advertised schema, after
  authentication, keeping the `Input validation error:` prefix.
- **`node-wire-bindings`**: advertised MCP tool schemas no longer carry Node Wire's internal
  `nw_in` / `nw_wire_name` / `nw_style` / `nw_explode` / `nw_media_type` routing metadata. It is
  read from the model fields at call time, not from the advertised copy, and was over a third of
  a generated connector's advertised input schemas. Applies to every connector, hand-written included.
- **`nw-connector-builder`** (**breaking** for generated tool shapes): object request bodies are
  flattened into top-level arguments — one typed, described field per body property, marked
  `nw_in="body_property"` — instead of a single `body` object. Callers send
  `{"channel": "C1", "text": "hi"}`, not `{"body": {...}}`. The generated Pydantic model is now
  the whole contract, so REST and gRPC enforce required body fields, types and string enums
  (`Literal`) exactly as MCP does; previously body contents were an unchecked `dict` outside MCP.
  A body property is required only when `requestBody.required` is true (it was ignored: `{}`
  passed for 60 Slack operations). Top-level `allOf` is merged, `readOnly` properties are
  skipped, a parameter sharing a body property's name becomes `<name>__<location>`, reserved
  names (`action`, `body`, `config_name`, `tenant_id`, `trace_id`) get `_param`, Python keywords
  a trailing `_`. Bodies that are not objects with declared properties keep one `body` argument,
  now typed from the schema (`list` / `str` / `dict`) and required when the body is.
- **`nw-connector-builder`**: parameters and body fields that duplicate the connector credential
  (`token`, `api_key`, `authorization`, … in a header, query or request body) are no longer
  emitted as call arguments on authenticated actions — the configured `AuthProvider` supplies
  them. Slack's spec declares a `token` header on every operation and a required form-body
  `token` on 11, which made the generated MCP tools demand an API token from their caller.
  Exact-name matching only: `page_token` / `cursor` / path parameters and anonymous actions are
  untouched, and drops are listed in the build report.
- **`nw-connector-builder`**: action names are cut to fit the MCP tool-name limit
  (`<connector_id>_<action>` ≤ 64 characters) at a word boundary, instead of at a fixed 40
  characters mid-word (`…_restrict_access_remo`, `…_add_`).
- **`nw-connector-builder`**: generated sources are `ruff format`ted at staging time with the
  target root's `[tool.ruff]` settings, so they pass the repo's `ruff format --check`; source ruff
  cannot parse fails the build. `ruff` is now a dependency of the builder.
- **`nw-connector-builder`**: generated packages require `node-wire-runtime>=1.1.0` (was
  `>=1.0.0`), the first runtime with the APIs generated code calls — a generated wheel could
  install next to 1.0.0 and fail at import on `RestEnvelopeError`.
- **`node-wire-runtime`** 1.1.0: `split_params_by_location` builds the request body from
  `nw_in="body_property"` fields, keyed by `nw_wire_name`, sending `{}` when none is set. The
  whole-body `nw_in="body"` field is unchanged; a model declaring both raises `ValueError`.
- **`node-wire-runtime`**: `MCP_MANIFEST_CONTRACT_VERSION` 5 → 6 — published tool shapes changed
  (flattened generated bodies, `nw_*` keys stripped, description moved out of `inputSchema`).
- **`node-wire-bindings`**: the MCP server builds the tool manifest once per set of loaded
  connectors instead of twice per tool call (name resolution and validation each rebuilt it,
  ~40 ms each for a 174-action connector). `list_tools()` returns copies.
- **`node-wire-bindings`**: the input model's docstring is published only as the tool
  description, no longer repeated as `inputSchema.description`.
- **`node-wire-bindings`**: tool descriptions no longer end with `Security & Limits: Requires
  Auth: Yes` and `Pass fields from inputSchema only; … Manifest contract vN.` on every tool —
  a quarter of a 174-tool connector's `tools/list`. Deprecation, scopes and rate limits are
  still noted when set; the contract version is published once in the server's `instructions`.
  Applies to every connector.
- **`nw-connector-builder`**: generated models publish schemas without `title`s — the model
  class name and the per-field titles Pydantic generates, which restate the field name
  (`channel` → `"Channel"`). Emitted as a `_drop_titles` `json_schema_extra` hook in
  `schema.py`; hand-written connectors are unaffected. With the description change above,
  Slack's `tools/list` goes from 124 KB to 79 KB as serialized by the MCP SDK (−37%).
- **`node-wire-bindings`**: an unexpected top-level argument that a nested object declares gets a
  hint — `Additional properties are not allowed ('channel' was unexpected) ('channel' belongs
  inside 'body')`.
- **`nw-connector-builder`**: specs carrying JSON Schema draft-4 constructs that OpenAPI 3.0
  forbids no longer fail the build. `type: [T, "null"]`, multi-type unions, a bare `type: "null"`,
  and tuple-form `items: [A, B]` are rewritten to their OAS 3.0 equivalents (`nullable`, `anyOf`)
  before validation, and Swagger 2.0 response-level `examples` are moved into
  `content.<mime>.example` during conversion. Slack's published Web API spec hit all of these and
  aborted at load; it now builds 174/174 operations with zero soft-drops.
- **`nw-connector-builder`**: building with a connector id whose `src/node_wire_<id>/` exists and
  was not written by the generator is now refused up front — before staging, because the build
  gate imports generated code in-process and its `declare_secret_shape` call would replace a
  hand-written connector's tenant-secret contract. `--force` does not override it: deleting
  hand-written source should be deliberate, not a build side effect.
- **`nw-connector-builder`**: generated packages now declare their own tenant-secret shape.
  `logic.py` calls `declare_secret_shape()` at import time with the auth plan's secret keys
  (connector-level scheme required, per-action scheme secrets format-checked but optional), so
  the config store accepts a generated connector's credentials under
  `NW_SECRET_SHAPE_POLICY=enforce` instead of rejecting it as unrecognised — previously only the
  hand-written connectors hard-coded in `node_wire_runtime/tenant_persistence.py` could be
  stored. Generated packages also ship a `README.md` recording the actions table and the
  credential contract.
- **`nw-connector-builder`**: every OpenAPI `oauth2` scheme (including `clientCredentials` and
  `authorizationCode`) and `openIdConnect` now maps to a host-supplied `static_token` bearer
  (`<ID>_ACCESS_TOKEN`). Generated connectors never call a token endpoint or refresh a grant —
  the host obtains and rotates the credential. Hand-written `oauth2` connectors
  (`fhir_epic`, `salesforce`, …) are unchanged (`nw-connector-builder-scope.md`,
  `nw-connector-builder.md`).
  **Migration** (only affects connectors generated from this unreleased branch — the previous
  `oauth2` derivation was never in a tagged release): regenerating such a spec with `--wire`
  replaces that connector's whole entry in `connectors.yaml`, so `provider: oauth2` plus
  `<ID>_CLIENT_ID` / `<ID>_CLIENT_SECRET` / `<ID>_REFRESH_TOKEN` becomes `provider: static_token`
  with `<ID>_ACCESS_TOKEN`. The old env vars are left in `sample.env` but are no longer read; the
  host must supply an access token under the new key. Hand-written `oauth2` connectors are not
  regenerated and keep working as-is.
- **PyPI distribution rename**: the generic HTTP connector publishes as
  `node-wire-http` (was `node-wire-http-generic`, never published under that
  name). The import package (`node_wire_http_generic`), the connector key, and
  the `node_wire.connectors` entry point (`http_generic`) are unchanged.

### Security

- **`nw gen-stacklok` manifests**: the backend no longer trusts `X-Tenant-ID` on the NetworkPolicy
  alone. `deploy/proxy-secret.yaml` holds a shared secret every tenant proxy sends as
  `X-NW-Proxy-Secret` (`headerForward.addHeadersFromSecret`); the backend reads it as
  `NW_PROXY_SECRET`, and `node-wire-toolhive` rejects tenant requests without it
  (`PROXY_AUTH_FAILED`). `deploy/README.md` states the NetworkPolicy-enforcing CNI requirement.
  The generated placeholder value `REPLACE_ME_PROXY_SECRET` is always rejected, so a
  `proxy-secret.yaml` applied unedited fails closed instead of sharing a publicly known secret.
- **Generated stacklok image**: `.dockerignore` now allow-lists `config/connectors.yaml` instead of
  excluding only `config/tenants.yaml`, so a tenants file under another name is never baked in.
- **Headless AI scoping**: Claude Code runs with `--permission-mode dontAsk` and file edits
  allowed only inside the scoping work directory (was `acceptEdits` across the repo), so a
  prompt-injected spec cannot rewrite `.claude/` or the vendored skill.
- **`nw gen-stacklok`**: `server.name` is checked as a DNS label when the scope is read, and
  `--force` refuses to delete a project outside the output directory.

### Fixed

- **nw-mcp-builder**: the MCP host project's `config/connectors.yaml` holds only its own
  connector. It used to copy the repo's whole file, so every start logged "Connector enabled in
  configuration but not registered" for each of the other connectors, and baked their base URLs
  and auth blocks into the image. A connector that isn't wired in now fails the build with a
  clear message.
- **node-wire-runtime**: OTLP log export skips record attributes OpenTelemetry can't carry.
  structlog's `_logger` object on every line of a stacklok-built server logged an "Invalid type …
  for attribute value" warning per line. The record keeps them for the console.
- **node-wire-runtime**: a failed connector run is logged at error level once, by
  `BaseConnector.run` with its `invocation_failure` audit line. The resilience layer's second
  "Non-retryable error during execution" line is now a debug-level retry decision.
- **node-wire-runtime**: connector metrics (`connector.action`), the `connector.run` span and
  the run's audit log lines now name the invoked action (the call's `action`). They said
  `execute`, the class default, for every call, so no per-action breakdown was possible.
- **node-wire-runtime**: span export failed in the compiled (Cython) wheels with
  `TypeError: Argument 'attributes' has incorrect type (expected dict, got BoundedAttributes)`, so
  no trace ever reached the collector. The source install was unaffected, which is why tests
  passed.
- **stacklok-built servers**: two kinds of tool failure now read
  `CODE [CATEGORY]: message (trace_id=...)` and are logged under that trace id, as
  `docs/stacklok-mcp-servers.md` says every failure does.
  - Invalid arguments were reported in pydantic's words, with no code or trace id. They're now
    `VALIDATION_ERROR [BUSINESS]: Input validation failed; channel: Field required ...`, via
    `node_wire_toolhive.report_call_errors`, which generated servers call when they build
    their MCP server.
  - `nw_list_configs` / `nw_select_config` failures (`TENANT_REQUIRED`, `CONFIG_NOT_FOUND`) had no
    trace id.

  Regenerate existing servers (`nw gen-stacklok --force`) to pick this up.
- **nw-connector-builder `--wire`**: `sample.env` is replaced atomically, like
  `config/connectors.yaml`, keeping its file mode. `nw gen-mcp` copies both files, so a
  concurrent `nw` run that was wiring a connector in could hand it a half-written file.
- **MCP host logging**: the runtime's taxonomy and trace fields (`trace_id`, `error_code` …) were
  dropped from the console by plain formatters. nw-mcp-builder hosts and stacklok-built servers
  now print them. A host that already has a handler with a plain `logging.Formatter` keeps its
  format and gains the fields. Redaction was installed only as a root-logger filter, which
  doesn't run for records from child loggers (`runtime.base_connector` …). It now sits on the
  root handlers too.
- **nw-mcp-builder**: the MCP project copies only wheels its image can install: CPython 3.13,
  glibc Linux, the image's CPU architecture. Before, it took the newest wheel in `dist/`, which
  could be gen-stacklok's musllinux build, a macOS or cp312 wheel, or the other architecture.
- **`nw gen-stacklok`**: image wheel stamps list their wheels, like `nw gen-whl`'s, so the
  `.nw-source-cp313-musllinux.sha256` stamps share one format.
- **Generated stacklok project**: every patch to stacklok's template (Dockerfile, pyproject,
  `mcp_builder.py`, `settings.py`, `__main__.py`) now fails the build with
  `TemplateChangedError` when its target isn't found, instead of silently doing nothing.
- **nw-connector-builder**: regenerating a connector with `--force` now keeps
  `packages/connectors/<id>/dist/`. Deleting it made `nw gen-stacklok` recompile the connector
  on every run, even from an unchanged spec, and made `--no-wheel` fail for want of a wheel.
- **`node-wire-bindings`**: two `ConnectorFactory` fixes.
  - `apikey_query` auth now reads the tenant-scoped secret (`NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}`)
    like every other provider. It used the factory-wide provider, so every tenant got the unscoped key.
  - A connector's `base_url` in `connectors.yaml` now overrides the generated connector's baked-in
    default. The YAML bootstrap stores it under the config document's `config` block, which the
    factory never read.
- **`node-wire-runtime`** / **`node-wire-bindings`** packaging: editable installs no longer
  compile the sources in place. The nw-* tools depend on both packages by path (editable);
  setuptools' `editable_wheel` ran the Cython `build_ext` in place and wrote `.so` files into
  `src/`, which Python imports ahead of the `.py` files — source edits silently stopped taking
  effect for everything using that checkout. `setup.py` now builds editable installs as pure
  Python; published wheels are compiled as before.

- **`nw-mcp-builder`**: scope-fixture tool names are no longer cut at 40 characters (and the
  fixture schema no longer rejects longer ones). They now follow the 64-character MCP limit
  nw-connector-builder sizes action names to, so the fixture names match what the server lists
  (`admin_conversations_restrict_access_remove_group`, not `…_remo`).

- **`nw-connector-builder`**: Swagger 2.0 `in: body` parameters no longer become a *required*
  OpenAPI 3 request body unless they say `required: true` (the 2.0 default is optional).
- **`nw-connector-builder`**: generated example tests emit spec examples as Python literals —
  a boolean or null in an example was written as JSON `true`/`null` and failed the staging gate
  with a `NameError`; YAML dates are rendered as strings.
- **`nw-connector-builder`**: parameter and body field names that are Python keywords (`from`,
  `class`) or `BaseModel` attributes (`json`, `copy`) are suffixed with `_` instead of producing
  a generated module that does not import.

- Google Drive, Slack, and SMTP log connector-scoped errors (with ``failed`` /
  ``error`` in the message and ``connector_id``) so Grafana's connector filter
  can count playground ``internal_execute`` failures instead of reporting 100%.

## [1.1.0] - 2026-07-30

### Added

- **`nw-cli`**: a new unified CLI (`nw`) for the OpenAPI → connector → wheel →
  MCP → Docker pipeline, with `gen-all` (one-shot), and standalone `gen-whl`,
  `gen-mcp`, and `docker-build` stages, prerequisite checks, and a Rich
  progress UI (`nw-cli/`, `docs/nw-cli.md`).
- **`nw-connector-builder`**: a new package that derives a connector
  (auth, naming, operations, normalizers) from an OpenAPI/Swagger spec and
  generates its codegen output, with a promotion gate for reviewing generated
  connectors before they're wired in (`nw-connector-builder/`,
  `docs/nw-connector-builder.md`, `docs/nw-connector-builder-scope.md`).
- **`nw-mcp-builder`**: a new package that generates a standalone MCP server
  project from an existing connector (`nw-mcp-builder/`), including
  mcp-builder YAML configs for the Slack and Salesforce connectors.
- Multi-tenancy support across all bindings (REST, gRPC, MCP) and the
  playground: per-tenant configuration and secrets, tenant selection, and
  per-tenant credential isolation for every connector (Google Drive, HTTP
  generic, SMTP, Stripe, Epic FHIR, Cerner FHIR, Salesforce, Slack).
- `node_wire_runtime.config_store` and `node_wire_runtime.tenant_persistence`
  for durable per-tenant configuration storage, an expanded `secrets`
  subsystem for per-tenant secret resolution, and `node_wire_runtime.identity`
  for tenant identity resolution shared across bindings.
- `TenantSessionOverlay` (`node_wire_runtime.tenant_session`), extracted from
  the MCP server's private state, to isolate per-request tenant/config context.
- Google Drive MCP upstream OIDC / bearer passthrough so MCP requests can forward
  the caller’s OAuth token to Drive.
- LLM provider/model switching in the playground (`agents/llm_factory.py`,
  new `agents/llm_base.py`), with new Anthropic and Gemini providers alongside
  updated OpenAI and Groq providers, and playground UI controls for selecting
  a model per session.
- Per-connector response normalizers (`normalizers.py`) for Google Drive,
  Salesforce, SMTP, Epic FHIR, and Cerner FHIR.
- Error taxonomy support in the connector codegen and mcp-builder pipelines.
- A new query-parameter API key auth mechanism (`node_wire_runtime.auth.apikey_query`).
- OpenTelemetry metrics and structured audit-trail events on connector
  invocations.
- REST `/ready` readiness endpoint (requires at least one REST or gRPC connector).
- Automated release-tag workflow (`create-tag.yml`) and a lockstep package
  version-bump script with changelog scaffolding.
- Branded documentation site with architecture overview, published from CI
  (`mkdocs.yml`, `docs/index.md`, `docs/mcp-servers.md`).
- `scripts/build-mcp-server.sh` and `scripts/mcp-servers.registry` for
  building per-connector MCP server images, and a `docs.yml` CI workflow to
  publish the documentation site.
- PR patch-coverage gate (80% overall, with an 80% floor on changed files) and
  static security analysis on pull requests.
- Broader automated test coverage for bindings, secrets, auth, metrics, and audit
  trail.

### Changed

- Collapsed the REST/gRPC binding invoke path into a shared `bindings/invoke.py`
  helper, removing duplicated dispatch logic between transports.
- Strengthened auth/scope-policy enforcement and connector isolation as part
  of a connector framework optimization pass; removed the per-connector
  `registration.py` modules and the shared `mcp_contract.py` /
  `mcp_normalizers.py` in favor of the new normalizer-based registration path.
- Rebuilt the wheel-build script for roughly an 80% reduction in build time.
- Pinned the MCP SDK version until the codebase is compatible with the newer
  release.
- `nw-mcp-builder` generated Dockerfiles now use the same digest-pinned
  `python:3.12-slim` base and non-root `USER` as checked-in images, with a
  whitelist `.dockerignore`, no secrets/`COPY .env` in the image, a read-only
  application tree, and no default `NW_MCP_AUTH_DISABLED` in containers.
- Node Wire branding refresh (README badges/logos, docs site theme).
- Documentation updates for connector authoring, MCP servers, packaging/release
  flow, configuration, architecture, multi-tenancy, and the connector builder
  pipeline (`docs/architecture.md`, `docs/configuration.md`, `docs/connectors.md`,
  `docs/mcp.md`, `docs/nw-connector-builder.md`, `docs/public-api.md`,
  `docs/toolhive_agent_scenario.md`).
- CI workflows hardened for consistency across quality, security, and publish
  checks.
- Publish workflow accepts PEP 440 pre-release tags (`aN` / `bN` / `rcN`) and
  skips the GitHub Release prerequisite for those tags so betas can ship to
  PyPI without a GitHub Release; Create Release Tag and `bump-version.py`
  accept the same version shapes (`docs/packaging.md`, `docs/versioning.md`).

### Fixed

- Moved `grpcio-tools` out of core `node-wire` dependencies into the optional
  `grpc-codegen` extra (used only by `scripts/generate-grpc-stubs.sh`). Plain
  installs and CI `uv sync --extra agents --dev` no longer pull a native
  compile of `grpcio-tools` (fixes Windows MSVC failures when no wheel matches).
- `ErrorMapper` now scopes error-code matching per connector ID with an
  MRO-specific match, fixing a cross-connector leak where one connector's
  error code could surface through another connector's error handling.
- Fixed a cross-tenant/cross-config race condition on the MCP HTTP transport
  where concurrent requests could read another tenant's session or
  configuration.
- Security and logging hardening: safer hashing, sanitized sensitive fields in
  logs, stricter URL handling, reduced exception information exposure, and related
  cleanups across runtime, agents, playground scenarios, and connectors.
- gRPC health-check behavior for readiness/liveness reporting.
- Log-sanitization test isolation so logger filters do not leak across the suite.

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

- Connector authentication misconfiguration now surfaces clear, actionable error
  messages instead of cryptic library exceptions:
  - **OAuth2 private_key_jwt** (`oauth2.py`): `jwt.InvalidKeyError` is caught and
    re-raised with the algorithm and a pointer to the `private_key_secret`
    configuration.
  - **OAuth2 token endpoint** (`oauth2.py`): Non-200 responses from the token URL
    now include the HTTP status, the token URL, and a preview of the server
    response rather than a bare `raise_for_status()` traceback.
  - **Google service account** (`service_account.py`, `google_drive/logic.py`):
    Invalid JSON reports the secret name; a missing key file reports the resolved
    path; malformed key structures surface the underlying Google library error
    with context.
  - **SMTP** (`smtp/logic.py`): `SMTPAuthenticationError` names
    `SMTP_USERNAME`/`SMTP_PASSWORD`; connect/disconnect errors name
    `SMTP_HOST`/`SMTP_PORT`/`SMTP_USE_TLS`; timeout errors mention `NW_TIMEOUT`.

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

[1.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.1.0
[1.0.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.0.0
[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0

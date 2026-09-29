# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

### Changed

- **All packages now require Python 3.13** (`requires-python = ">=3.13"`), covering the runtime,
  bindings, connectors, `nw-cli`, `nw-connector-builder` and `nw-mcp-builder`. Wheels are built
  for `cp313` only (`CIBW_BUILD=cp313-*`; the publish matrix is one job per platform). Container
  images and the generated MCP host Dockerfile use `python:3.13-slim` (digest-pinned), connectors
  generated by `nw-connector-builder` declare `>=3.13`, and CI runs on 3.13. Package versions are
  unchanged. Branch protection must require `Run pytest (ubuntu-latest, Python 3.13)` in place of
  the 3.11/3.12 checks.

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

### Fixed

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

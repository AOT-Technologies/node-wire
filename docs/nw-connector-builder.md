<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder

Self-contained tool inside the **node-wire** repo with two subcommands:

| Command | Purpose |
|---------|---------|
| `nw-connector-builder from-openapi` | Turn a **Swagger 2.0** / **OpenAPI 3.x** document into a `node_wire_<id>` connector (and optionally an MCP host) |
| `nw-connector-builder mcp` | Generate an MCP host from an **existing** connector (same as standalone `nw-mcp-builder`) |

Use `from-openapi` when the upstream API already ships an OpenAPI/Swagger spec and you want a first-class Node Wire `RestConnector` instead of hand-writing schemas and `@nw_action` methods. Use `mcp` (or [nw-mcp-builder](mcp-servers.md)) for hand-written connectors. For the full happy path (codegen → Linux wheels → MCP host → wire → Docker), prefer the [`nw` CLI](nw-cli.md). For SDK-style or non-REST adapters, follow the hand-written path in [connectors.md](connectors.md).

---

## What this folder is

| Path | Purpose |
|------|---------|
| `nw-connector-builder/src/nw_connector_builder/` | CLI, load/normalize, derive, codegen, gate, promote, wire, MCP hand-off |
| `tests/nw_connector_builder/` | Pipeline and unit tests (run from the **node-wire** repo root) |

Generated output lands in the monorepo (not under `nw-connector-builder/`):

| Output | Location |
|--------|----------|
| Connector package | `src/node_wire_<id>/` |
| Publishable package metadata + model tests | `packages/connectors/<id>/` |
| Build report | `packages/connectors/<id>/report.json` (success) or `./report.json` (abort) |
| MCP host (unless `--no-mcp`) | `nw-mcp-builder/out/<name>-mcp/` |

---

## What it does (end to end)

```mermaid
flowchart LR
  spec[OpenAPI / Swagger]
  load["Load spec<br/>(file or URL)"]
  normalize["Normalize<br/>Swagger 2.0 → 3.x"]
  validate["Resolve refs<br/>+ validate"]
  derive[Derive actions]
  stage[Stage codegen]
  gate[Import + pytest gate]
  promote[Promote to repo]
  mcp[MCP hand-off]
  wire["--wire config"]
  spec --> load --> normalize --> validate --> derive --> stage --> gate --> promote
  promote --> mcp
  promote --> wire
```

For a connector id like `pet_store`:

1. **Load** — read the spec document from a local file path or `http(s)` URL (`--path`). URL fetches go through Node Wire's SSRF guard (`assert_safe_destination`) and never follow redirects. The raw bytes are decoded as UTF-8 and parsed as YAML/JSON into a document, then the version is detected from `swagger: "2.0"` vs. `openapi: "3.x"`.
2. **Normalize** — Swagger 2.0 documents are translated into OpenAPI 3.x in-house; OpenAPI 3.x documents pass through unchanged.
3. **Validate** — reject any **remote** (absolute-URL) `$ref`s outright (only local relative / in-document `#/` refs are allowed), resolve the remaining local `$ref`s in place with `prance`, then structurally and semantically validate the fully-resolved document with `openapi-spec-validator`. This step also resolves the connector's base URL (`--base-url` override, else the first `servers[]` entry).
4. **Derive** — from the validated document, work out the connector's auth plan (one connector-level default scheme, plus any additional per-action schemes — see [Auth mapping](#auth-mapping) below) and, per operation, either a plan for a generated `@nw_action` or a soft-drop reason (unsupported/divergent-and-unpresentable/AND-multi security, unsupported parameter styles, etc. — see [Soft-drop rules](#soft-drop-rules)).
5. **Codegen** — write a temp staging tree: `schema.py` (Pydantic input/output models) and `logic.py`, a `RestConnector` subclass with one `@nw_action`-decorated async method per derived action. Each method's `@nw_action(...)` decorator (`requires_auth=False` for anonymous actions) makes it discoverable by `nw-mcp-builder`'s regex scan; the method body itself calls `self.execute_rest(...)`, passing `auth_scheme=<name>` when the action uses a named, non-default scheme from step 4. Also emits the package `pyproject.toml` and model tests.
6. **Gate** — import smoke + `pytest` on staged model tests (must pass before promote)
7. **Promote** atomically into `src/node_wire_<id>/` and `packages/connectors/<id>/`
8. **MCP hand-off** (default) — call `nw-mcp-builder`, which regex-scrapes the promoted `logic.py` for `@nw_action` methods, for `out/<name>-mcp/`
9. **Wire** (optional `--wire`) — update `config/connectors.yaml` and `sample.env`

Promote never runs if the gate fails. MCP or `--wire` failures after a clean promote return exit code `1` but leave the connector in the tree.

---

## Requirements

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or an editable install of the package
- Run from / against a **node-wire** checkout (default `--node-wire-root` is the parent of `nw-connector-builder/`)
- For URL specs: network access; fetches use Node Wire’s HTTP safety checks (`assert_safe_destination`) and do not follow redirects
- MCP hand-off needs **`nw-mcp-builder`** importable (declared as a path dependency of this package)

---

## Quick start

### 1. Install / sync the tool

```bash
cd nw-connector-builder
uv sync
```

From the node-wire repo root:

```bash
uv run --directory nw-connector-builder nw-connector-builder --help
```

### 2. Generate a connector

```bash
# Local file — connector only (skip MCP)
uv run --directory nw-connector-builder nw-connector-builder from-openapi \
  --path path/to/openapi.yaml \
  --id my_api \
  --no-mcp

# Remote Swagger/OpenAPI — overwrite if present, wire config, build MCP host
uv run --directory nw-connector-builder nw-connector-builder from-openapi \
  --path https://petstore.swagger.io/v2/swagger.json \
  --id pet_store \
  --force \
  --wire
```

`--id` must match `[a-z][a-z0-9_]*` (e.g. `pet_store`, not `PetStore`).

To regenerate an MCP host from an existing connector (no OpenAPI step):

```bash
uv run --directory nw-connector-builder nw-connector-builder mcp \
  -c pet_store --force-output
```

### 3. Run and verify

After a successful promote (and usually `--wire`):

```bash
# Ensure the connector is allowlisted (done automatically with --wire)
# NW_ALLOWED_CONNECTORS=...,pet_store

MODE=API uv run node-wire
```

Open [http://localhost:8000/docs](http://localhost:8000/docs). Generated actions appear under the new connector.

If MCP was generated:

```bash
cd nw-mcp-builder/out/pet-store-nw-mcp
cp .env.example .env   # fill secrets
uv sync
uv run python -m pet_store_nw_mcp
```

See [mcp-servers.md](mcp-servers.md) for ToolHive, Linux wheels, and Inspector.

### 4. Tests for the builder itself

From the **node-wire** repo root:

```bash
uv run pytest tests/nw_connector_builder -v --no-cov
```

---

## CLI reference

```
nw-connector-builder from-openapi --path <SPEC> --id <connector_id> [options]
nw-connector-builder mcp -c <connector_id> [options]
```

Legacy flat flags (`nw-connector-builder --path … --id …`) still map to `from-openapi`.

### `from-openapi`

| Option | Default | Description |
|--------|---------|-------------|
| `--path` | — (required) | Local path or `http(s)` URL to an OpenAPI/Swagger document |
| `--id` | — (required) | Connector id (`[a-z][a-z0-9_]*`) |
| `--wire` | off | After promote, edit `config/connectors.yaml` + `sample.env` |
| `--force` | off | Overwrite an existing connector tree; pass `--force-output` to MCP hand-off |
| `--no-mcp` | off | Stop after a clean connector promote (skip MCP host generation) |
| `--base-url` | `servers[0]` | Override baked-in default base URL (required if the spec has no absolute server URL) |
| `--node-wire-root` | Parent of `nw-connector-builder/` | Monorepo root used for promote / wire / MCP |
| `--report-path` | `./report.json` | Where to write `report.json` on **abort** (success writes beside the package) |
| `-v`, `--verbose` | off | Debug logging |

### `mcp`

Same flags as standalone `nw-mcp-builder` (see [mcp-servers.md](mcp-servers.md)): `-c` / `--connector-id`, `--force-output`, `--force-fixture`, `--skip-build-wheels`, `-o` / `--output-dir`, `--fixtures-dir`, `--python`, `--node-wire-root`, `-v`.

Help:

```bash
uv run --directory nw-connector-builder nw-connector-builder --help
uv run --directory nw-connector-builder nw-connector-builder from-openapi --help
uv run --directory nw-connector-builder nw-connector-builder mcp --help
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Clean build (gate + promote OK; MCP/wire OK or skipped) |
| `1` | Hard failure (load/derive/gate) **or** post-promote MCP/`--wire` failure |
| `2` | Usage error (bad `--id`, destination exists without `--force`, etc.) |

---

## Spec loading rules

| Topic | Behavior |
|-------|----------|
| Formats | YAML or JSON, UTF-8 |
| Versions | Swagger `2.0` (normalized to OpenAPI 3) or OpenAPI `3.x` |
| Sources | Local file path, or `http` / `https` URL |
| Remote `$ref` | **Rejected** — absolute remote refs are not fetched; keep a self-contained document (local relative/`#/` refs OK) |
| Validation | Resolved with `prance` + validated with `openapi-spec-validator` |
| Draft-4 repair | Constructs JSON Schema draft-4 allows but OpenAPI 3.0 forbids are rewritten before validation, not rejected (see below) |
| Base URL | From `--base-url`, else first `servers[]` entry with substitutable defaults; relative-only servers hard-fail |

### Draft-4 repair

Real-world specs — Slack's published Web API spec among them — carry JSON Schema draft-4 constructs that OpenAPI 3.0's Schema Object does not allow. One of them anywhere in the document fails validation and takes the whole build with it, so the loader rewrites them into their OAS 3.0 equivalents (after `$ref` resolution, so inlined targets are covered) and logs a count of what it changed:

| Found | Rewritten to |
|---|---|
| `type: ["string", "null"]` | `type: string` + `nullable: true` |
| `type: ["string", "integer"]` | `anyOf: [{type: string}, {type: integer}]` |
| `type: "null"` | `nullable: true` |
| `items: [A, {type: null}]` | `items: A` + `nullable: true` |
| `items: [A, B]` | `items: {anyOf: [A, B]}` |

Tuple-form `items` means positional validation in draft-4, which no generated model can express; the specs that use it mean "A or B", so that is how it is read. Documents without these constructs are untouched.

Swagger 2.0 input gets one more repair during conversion: response `examples` (keyed by mime type on the response object, which OAS 3 has no property for) moves to `content.<mime>.example`.

---

## Auth mapping

The builder picks **one connector-level default** security scheme (document `security`, else the most common scheme across operations) and maps it to a Node Wire auth provider for `--wire` / `connectors.yaml`:

| OpenAPI scheme | Node Wire `auth.provider` | Typical secret env |
|----------------|---------------------------|--------------------|
| `apiKey` in `header` | `static_token` | `<ID>_API_KEY` |
| `apiKey` in `query` | `apikey_query` | `<ID>_API_KEY` |
| `http` + `bearer` | `static_token` | `<ID>_TOKEN` |
| `http` + `basic` | `static_token` (`prefix: Basic`, base64) | `<ID>_BASIC_AUTH` |
| `oauth2` (any flow) or `openIdConnect` | `static_token` (`prefix: Bearer`, `host_supplied: true`) | `<ID>_ACCESS_TOKEN` |
| None / unsupported only | `none` (anonymous) | — |

Secret names come from the connector id and the credential kind, so a second scheme of the same
kind would otherwise reuse the first one's secret. When that happens the extra scheme is emitted
under `auth_schemes:` with its name folded in — `<ID>_<SCHEME>_ACCESS_TOKEN`,
`<ID>_<SCHEME>_API_KEY`, and so on. The build report's
`auth.notes` name the scheme and, when the OpenAPI document declares them, the
`authorizationUrl` / `tokenUrl` for the host to call — those URLs are documentation only;
the connector never POSTs to a token endpoint.

### Host-supplied tier

Every `oauth2` scheme and `openIdConnect` map to a **host-supplied** bearer token: Node Wire
presents whatever value sits in `<ID>_ACCESS_TOKEN` as a plain `Bearer` header but never
obtains, refreshes, or detects the expiry of it. The operator's host application is responsible
for acquiring and rotating that token out-of-band. This is presentation only — the acquisition
ban on all OAuth2 / OIDC flows for *generated* connectors is deliberate; see
[nw-connector-builder-scope.md](nw-connector-builder-scope.md#host-supplied-auth-tier). The build
report's `auth.notes` spell out which scheme triggered it and the exact secret key to set.

**"Presents a bearer token" is not the same claim as "supports the flow."** At runtime this tier
is `StaticTokenAuthProvider` with `cache=False`, which re-reads the secret per call — a host that
replaces the value is seen on the next call for live-resolving secret providers (env, overlay);
backends that snapshot at init (AWS/GCP/Vault) need the provider recreated, and Azure Key Vault is
better served by the cached path plus an explicit `refresh()`. Node Wire never calls a token
endpoint or performs a grant exchange. Hand-written connectors that already use `OAuth2AuthProvider` (`fhir_epic`,
`salesforce`, …) are unchanged. See [nw-connector-builder-scope.md](nw-connector-builder-scope.md#host-supplied-auth-tier).

### Per-action auth schemes

Operations that require a **different, still-presentable** scheme than the connector-level default (self-managed or host-supplied — anything except `mutualTLS`, cookie `apiKey`, AND-multi, or an unrecognized type) are no longer soft-dropped as divergent. Instead the builder emits an **additional, named** scheme in `auth_schemes:` (alongside the default `auth:` block) and routes just those actions to it via `auth_scheme=<scheme_name>` on the generated `@nw_action` call — the runtime resolves it with `resolve_auth_provider(auth_scheme)`, which fails closed on an unknown name. This is what lets one connector serve multiple OpenAPI security schemes from a single instance — e.g. `petstore.swagger.io`, where most operations use an `apiKey` default but a handful require an `oauth2` (`implicit`) scheme that's now generated as a host-supplied `auth_schemes` entry instead of being dropped.

**Still soft-dropped** as unsupported (operations that require only these are skipped):

- `mutualTLS`
- Cookie API keys (`apiKey` `in: cookie`)
- AND multi-scheme requirements (`security: [{ a: [], b: [] }]`)
- Unrecognized scheme types

---

## Tool and parameter descriptions

Generated tools carry the spec's own text, so an MCP client can tell what a tool does and what each argument expects:

- **tool description** — the operation's `summary`, else its `description`; first paragraph only, markdown links flattened to their text, capped at 300 characters. It becomes the input model's docstring, which the manifest publishes as the tool description.
- **parameter description** — from the parameter, or its schema (OAS 3 allows either); first sentence, capped at 90 characters.

Everything past the first paragraph (argument tables, error lists) repeats the input schema and is paid for in every tool listing, so it is left out. On Slack's spec no operation description reaches the cap.

---

## Action names and generated source

- **Action names** come from `operationId` (or method + path), snake_cased. They are cut to fit the MCP tool-name limit — the tool name is `<connector_id>_<action>`, at most 64 characters — at a word boundary, never mid-word and never leaving a trailing `_`. Duplicates get a numeric suffix.
- **Formatting** — generated `src/node_wire_<id>/` and its tests are run through `ruff format` at staging time, using the target root's `pyproject.toml` `[tool.ruff]` settings, so they pass the same `ruff format --check` as hand-written code. Source ruff cannot parse fails the build: it is a codegen bug.
- **No schema titles** — generated models carry a `_drop_titles` `json_schema_extra` hook, so their JSON schemas have no `title` on the model or its fields (Pydantic's defaults only restate the names). Descriptions carry each field's meaning.
- **Runtime floor** — the generated package requires `node-wire-runtime>=1.1.0`, the first runtime with `body_property` routing and success-flag envelopes.

---

## Request body fields

A generated tool takes **one flat set of arguments**. Each property of an object request body becomes its own typed, described argument next to the path, query and header parameters — there is no nested `body` object to fill in:

```json
{"channel": "C0123", "text": "hi"}
```

The tool contract (what a caller sends) is kept separate from the wire contract (where each value goes on the HTTP request). Every generated field carries its location in `nw_in`; body properties use `nw_in="body_property"`, and the runtime rebuilds the request body from them by wire name (`split_params_by_location` in `node_wire_runtime/rest.py`). The same Pydantic model is validated by every binding, so REST, gRPC and MCP all enforce the same required fields, types and enums.

Rules:

- **Required** — a body property is required only when the request body is required (`requestBody.required: true`) *and* the property is in the body schema's `required` list. An optional body may be omitted entirely.
- **Always sent** — an operation that declares a body sends one, `{}` when no property is set.
- **`allOf`** parts at the top of the body schema are merged; `readOnly` properties are left out (a client never sends them).
- **Enums** on string fields become `Literal[...]` types, so they are enforced, not only advertised.
- **Name clashes** — a path/query/header parameter that shares a name with a body property is renamed `<name>__<location>` (e.g. `channel__query`); the body property keeps the plain name. Names the bindings reserve (`action`, `body`, `config_name`, `tenant_id`, `trace_id`) get a `_param` suffix, Python keywords a trailing `_` (`from` → `from_`, which also accepts `from`). The value is always sent under its original wire name.
- **Whole bodies** — bodies that are not objects with declared properties keep a single `body` argument: arrays (`list`), binary/string bodies (`str`), free-form maps, `oneOf`/`anyOf`, and media types other than JSON, form and multipart. `body` is required when the request body is.

---

## Credential parameters

Specs often declare the credential as an ordinary parameter — Slack's declares a `token` header on every operation. Generated verbatim, that becomes a required tool argument, so an MCP client asks its caller for the API token and pastes a secret into a tool call, while the connector is already attaching that credential itself from `<ID>_ACCESS_TOKEN`.

For **authenticated** actions the builder drops header, query and request-body fields whose name is exactly one of `token`, `access_token`, `accesstoken`, `api_key`, `apikey`, `api-key`, `auth_token`, `authorization`, `password`, `secret`. The configured `AuthProvider` supplies the value; on a collision the runtime already gives auth precedence (`rest.py`).

Matching is exact and scoped, so nothing else is affected:

- `page_token`, `next_token`, `cursor`, `token_id` — kept (not credentials)
- path parameters — kept, even when named `token`; they are part of the URL
- **anonymous** actions — kept, since no provider would supply the value
- a form-body `token` (Slack sends it that way on 11 operations) — dropped like a header one, including from the required list

Dropped parameters are named in the build report's notes.

---

## Success-flag envelopes (`ok: false`)

Some APIs never use HTTP error statuses for business failures — Slack answers `200 OK` with `{"ok": false, "error": "invalid_auth"}`. Nothing above the REST executor can tell that apart from a successful call, so the connector would report success for a failed request.

When an operation's success schema declares **`ok` as a required boolean**, the builder passes `envelope_ok_field="ok"` to `execute_rest` and adds `RestEnvelopeError` to the connector's `error_map` as a `BUSINESS` failure (`API_ENVELOPE_ERROR`). At runtime a body whose `ok` came back `false` raises instead of returning; the exception message carries the payload's `error` string when there is one (truncated to 200 chars).

The trigger is deliberately narrow so ordinary specs are unaffected:

| Success schema | Checked? |
|---|---|
| `required: [ok]`, `ok: {type: boolean}` | yes |
| `ok` present but **not** required | no — an omitted flag says nothing, and treating absence as failure would break valid responses |
| `ok` required but not a boolean | no |
| no `ok` property, or no documented success schema | no |

A missing flag at runtime is never a failure either: only a flag the API actually sent as `false` raises. Specs without the convention generate byte-identical code to before — no import, no `error_map` entry, no kwarg. The build report names the actions that got the check.

`envelope_ok_field` is off by default on `execute_rest`, so hand-written connectors are unaffected.

---

## Soft-drop rules

Unsupported operations are **skipped** (listed in the report) rather than aborting the build — unless **zero** operations remain (hard failure).

Common soft-drop reasons:

- Unsupported / divergent / AND-multi security
- `in: cookie` parameters
- Unsupported serialization styles (e.g. query `deepObject`, non-`simple` path/header styles)
- Unresolved parameter `$ref`s after the load step

A **coverage warning** is printed when fewer than 50% of document operations were generated.

Path/operation-level `servers` are ignored in v1 (noted in the report).

---

## Generated layout

After promote:

```
src/node_wire_<id>/
  __init__.py
  schema.py          # Pydantic input models + outputs (or RestResponseOutput)
  logic.py           # RestConnector subclass with @nw_action methods + declare_secret_shape()
  README.md          # actions table + the credential contract for this connector

packages/connectors/<id>/
  pyproject.toml     # entry point + deps
  report.json        # full build report
  tests/
    test_<id>_models.py   # polyfactory round-trip (+ example parse when present)
```

Generated files are marked `# Generated by nw-connector-builder — do not hand-edit.` Prefer regenerating with `--force` over hand-patching; if you must customize, treat the tree as owned source and stop overwriting it.

Complex request bodies and non-object success responses use permissive typing (`Any` / `RestResponseOutput`) for robustness.

---

## Staging gate

Before promote, the builder:

1. **Import smoke** — load `node_wire_<id>.logic`, find a `RestConnector` with matching `connector_id`, require at least one `@nw_action`
2. **pytest** — run staged `packages/connectors/<id>/tests` with `PYTHONPATH` pointing at staged `src/`

Gate failure writes `report.json` to `--report-path` (default cwd) and exits `1` without mutating the repo.

---

## Promote semantics

Promote is a two-phase commit across both trees (`src/node_wire_<id>` and `packages/connectors/<id>`):

1. Copy staging → `*.promoting` siblings
2. Move existing destinations aside to `*.bak` (if any)
3. Rename `*.promoting` → final paths
4. On failure, restore backups and discard promoting leftovers

If either destination already exists, you must pass **`--force`**.

`--force` only replaces output this tool wrote. If `src/node_wire_<id>/logic.py` exists without the `# Generated by nw-connector-builder` marker, the build is refused before staging — a hand-written connector sharing the id would otherwise be deleted, and the generated package's `declare_secret_shape()` call would replace that connector's tenant-secret contract, so stored credentials stop validating. Pick a different `--id`, or move the existing package aside first. The check runs before staging because the gate imports the generated module in-process.

---

## `--wire` behavior

When `--wire` is set after a clean promote:

**`config/connectors.yaml`** — upserts. The connector's entry is **replaced wholesale**, not merged: a previously derived `auth:` block (and any keys hand-added under that connector) is discarded and rewritten from the current auth plan. Regenerating a spec whose auth mapping changed therefore changes which secrets the connector reads.


```yaml
connectors:
  <id>:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    base_url: <derived or --base-url>
    auth:   # omitted when anonymous
      provider: ...
      secret_key: ...
    auth_schemes:   # only present when a divergent scheme needed its own auth_scheme=
      <scheme_name>:
        provider: ...
        secret_key: ...
```

**`sample.env`** — appends the connector to `NW_ALLOWED_CONNECTORS` and adds empty placeholders for derived secret keys (e.g. `PET_STORE_API_KEY=`, and `PET_STORE_ACCESS_TOKEN=` for a host-supplied scheme).

Wire edits preserve YAML comments via `ruamel.yaml`. Failures set exit code `1` but do not roll back the promoted connector.

---

## MCP hand-off

Unless `--no-mcp` is set, the builder calls `nw-mcp-builder` with:

- `force_fixture=True` (fixture must track the newly promoted connector)
- `force_output=<value of --force>`

MCP details (wheels, ToolHive, Inspector) live in [mcp-servers.md](mcp-servers.md). A failed hand-off returns exit code `1` after a successful promote — re-run either of these once the connector tree is good:

```bash
uv run --directory nw-connector-builder nw-connector-builder mcp -c <id> --force-output
# equivalent:
uv run --directory nw-mcp-builder nw-mcp-builder -c <id> --force-output
```

---

## Build report

Stdout summary plus JSON:

| Field | Meaning |
|-------|---------|
| `summary` | id, source, version, content hash, counts, errors |
| `generated_actions` | method, path, action name, auth flag |
| `skipped` | soft-dropped ops + reasons |
| `auth` | chosen provider / secret key / yaml block |
| `gate` / `mcp` / `wire` | stage outcomes when run |

On success: `packages/connectors/<id>/report.json`  
On abort: `--report-path` or `./report.json`

---

## Environment variables (generated connectors)

Generated `RestConnector`s honor the same egress controls as other REST adapters:

| Variable | Purpose |
|----------|---------|
| `NW_REST_ALLOWED_HOSTS` | Egress allowlist for outbound HTTP |
| `NW_REST_TRUST_ENV` | Set `true` to honor `HTTP(S)_PROXY` (default off) |

Plus connector-specific secrets from the auth plan (`<ID>_API_KEY`, `<ID>_TOKEN`, …) when using `--wire` / `sample.env`.

### Storing those secrets per tenant

`sample.env` is the single-tenant path. For per-tenant/per-config credentials the values go through the config store, which keeps a registry of which logical secrets each connector owns — hand-written connectors are hard-coded in `node_wire_runtime/tenant_persistence.py`, and generated ones declare themselves. Each generated `logic.py` therefore ends with:

```python
declare_secret_shape(
    "slack_web",
    required=["SLACK_WEB_ACCESS_TOKEN"],
    formats={"SLACK_WEB_ACCESS_TOKEN": "opaque_secret"},
)
```

It runs at import time (the entry point imports `logic`), so the store accepts this connector's credentials even under `NW_SECRET_SHAPE_POLICY=enforce`, which otherwise rejects any connector it does not recognise. The connector-level scheme's secrets are `required`; per-action scheme secrets are format-checked but optional, so a host can store the default credential without provisioning every scheme. Connectors with no auth declare `declare_secret_shape("<id>")` — an explicit "no tenant secrets" rather than silence.

---

## After generation — publishing checklist

`nw-connector-builder` creates the **runtime + package skeleton**. To ship on PyPI or as a standalone MCP Docker image, still complete the Tier 2 / Tier 3 steps in [packaging.md](packaging.md):

- [ ] Add `packages/connectors/<id>/setup.py` (Cython build glue) if publishing binary wheels
- [ ] Register the entry point in the **root** `pyproject.toml` for editable monorepo installs (if not already covered by your workflow)
- [ ] Add the path to `scripts/build-packages.sh` (`ALL_PACKAGES`) and CI allowlists (`nw gen-all` without `--no-wire` inserts the `ALL_PACKAGES` entry; CI allowlists stay manual)
- [ ] Update the package inventory in [packaging.md](packaging.md)
- [ ] Optional standalone MCP image rows in [mcp-servers.md](mcp-servers.md) / `docker-compose.mcp.yml` (the thin host under `nw-mcp-builder/out/` is separate from repo `docker/<name>/` images)

---

## Related docs

| Doc | When to read it |
|-----|-----------------|
| [nw-cli.md](nw-cli.md) | Orchestrated codegen → wheels → MCP → Docker |
| [connectors.md](connectors.md) | Hand-written connectors, `BaseConnector`, auth patterns |
| [mcp-servers.md](mcp-servers.md) | Running / packaging the generated MCP host |
| [packaging.md](packaging.md) | Wheels, PyPI, CI allowlists |
| [configuration.md](configuration.md) | `connectors.yaml` and env vars |
| [local-packages-to-images.md](local-packages-to-images.md) | Wheel → Docker image workflow |

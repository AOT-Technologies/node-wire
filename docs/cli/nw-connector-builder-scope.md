<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder — scope

Companion pages: [running the generator](nw-connector-builder.md) ·
[codegen behaviour](nw-connector-builder-codegen.md).

`nw-connector-builder` targets a specific, common shape of REST API (single connector-level
auth scheme, JSON-first bodies, no pagination) and **soft-drops** anything outside that shape
rather than trying to support every corner of OpenAPI/Swagger. This page is the scope
reference: what the generator handles today, what it deliberately does not, and what's
skipped-but-flagged per build. For usage, flags, and the codegen pipeline itself, see
[nw-connector-builder.md](nw-connector-builder.md).

---

## In scope

### Spec ingestion

- Swagger **2.0** and OpenAPI **3.x**, YAML or JSON, UTF-8
- Local file path or `http(s)` URL (remote fetch goes through Node Wire's SSRF guard,
  `assert_safe_destination`, no redirects followed)
- In-house Swagger 2.0 → OpenAPI 3.0 normalization at the front door
- Local relative `$ref`s and in-document `#/` refs
- Structural + semantic validation via `prance` + `openapi-spec-validator`

### Auth

- One connector-level **default** scheme, chosen from the document's top-level `security` (or the
  most common scheme across operations if none is declared)
- `apiKey` in `header` or `query`, `http` `bearer`, `http` `basic` — mapped to Node Wire's
  `static_token` / `apikey_query` auth providers (self-managed presentation)
- Every `oauth2` flow (`clientCredentials`, `authorizationCode`, `implicit`, `password`, or
  none declared) and `openIdConnect` — mapped to a **host-supplied** bearer (`static_token`,
  `host_supplied: true`): Node Wire presents `<ID>_ACCESS_TOKEN` verbatim as a `Bearer` header
  but never acquires, refreshes, or detects the expiry of it — see "Host-supplied auth tier"
  below. The host owns minting and rotation; the connector only attaches the token.
- Operations needing a **different, still-presentable** scheme than the connector default are not
  dropped — the generator emits it as an additional, named entry in `auth_schemes:` and routes just
  those actions to it (`auth_scheme=<name>` per `@nw_action`, resolved at runtime by
  `resolve_auth_provider`, which fails closed on an unknown name). A connector is no longer
  necessarily single-scheme; see "Per-action auth schemes" below.
- Anonymous connectors (no scheme, or only unsupported schemes present) build as `auth: none`

#### Host-supplied auth tier

Generated connectors follow the hand-written Slack ownership rule: the host obtains and rotates
the credential; the connector only attaches it. Every `oauth2` scheme and `openIdConnect` map to
a `static_token` provider marked `host_supplied: true` that presents `<ID>_ACCESS_TOKEN` as a
`Bearer` header with **no** acquisition, refresh, or expiry detection. The host application owns
obtaining and rotating that token entirely out-of-band; a stale token surfaces as a plain `401`
from the upstream API. The build report's `auth.notes` name the triggering scheme, the secret
key to set, and (when present in the spec) the `authorizationUrl` / `tokenUrl` for the host to
call — those URLs are documentation only.

**This is not OAuth2 (or OIDC) client support — it's a static bearer, full stop.** At runtime,
`host_supplied: true` configures `StaticTokenAuthProvider(cache=False)`, which re-reads the secret
on every call instead of caching the header. Whether a rotation is actually picked up is a
property of the configured `SecretProvider`, not of this flag: env and overlay providers resolve
live (so the new value is seen on the next call), while `AwsSecretsManagerProvider`,
`GcpSecretManagerProvider` and `HashiCorpVaultProvider` load their bundle at init and keep serving
it until recreated. `AzureKeyVaultProvider` does resolve live but makes a blocking network call per
read, so a Key Vault deployment should stay on the cached path and call `refresh()` on rotation.
Either way Node Wire never calls a token endpoint, never performs a grant exchange, never
negotiates scope, never authenticates as a client. Hand-written connectors that already use
`OAuth2AuthProvider` (`fhir_epic`, `salesforce`, …) are unchanged — this policy applies to
**generated** connectors only.

**One credential per connector, not per caller.** `<ID>_ACCESS_TOKEN` resolves once per
tenant/config scope, so every caller of a generated connector reaches the upstream API as the same
identity. That is the right shape for a service credential the host owns and rotates. It is *not*
per-user auth: if each caller must reach the vendor API as themselves, the mechanism is the
runtime's `provider: upstream_bearer` relay, which forwards the inbound request's own bearer token
per call and fails closed unless the connector is also listed in `NW_UPSTREAM_BEARER_CONNECTORS`
(see [`connectors.md`](../connectors.md#supported-provider-types) and
[`google_drive_connector.md`](../google_drive_connector.md#upstream_bearer)). The generator never
emits `upstream_bearer`: nothing in an OpenAPI document says the caller's own token is the right
credential to relay downstream, and relaying one to the wrong audience leaks it — so that stays a
deliberate hand-wiring step.

#### Per-action auth schemes

A connector can serve **more than one** OpenAPI security scheme from a single instance. The
connector-level default (`auth:` in `connectors.yaml`) still covers most operations; any operation
whose declared security is a *different* scheme — but one that's still presentable (self-managed or
host-supplied, i.e. not `mutualTLS`, cookie `apiKey`, AND-multi, or unrecognized) — gets its own
named entry under the new, additive `auth_schemes:` config block, and the generated action passes
`auth_scheme=<name>` so the runtime resolves the right `AuthProvider` per call instead of per
connector. This is what let petstore-style specs (an `apiKey` default alongside operations gated by
an `oauth2` `implicit` scheme) stop soft-dropping those operations. Secret names are derived from
the connector id and the credential kind, so two schemes of the same kind would otherwise share one
secret; when an extra scheme's secret name is already taken, it is qualified with the scheme name
(`<ID>_<SCHEME>_ACCESS_TOKEN`, `<ID>_<SCHEME>_API_KEY`, …) and the first claimant keeps the short
name.

### Codegen

- Actions discoverable via `@nw_action` on a generated `RestConnector` subclass (regex-scrapable
  by `nw-mcp-builder`, matching the hand-written-connector convention)
- A shared, hardened REST executor (`node-wire-runtime`) owns base URL, path templating,
  parameter placement, auth injection, SSRF checks, and error mapping
- Hybrid schema translation: `datamodel-code-generator` for request/response schemas, hand-rolled
  per-operation input envelope with an `action: Literal` discriminator
- Non-JSON request bodies (form data, files, raw content) encoded by declared media type
- Typed output models from the lowest documented 2xx JSON response, falling back to a generic
  response envelope when no schema is documented
- A `declare_secret_shape()` call per generated package, so the config store accepts the
  connector's tenant secrets under `NW_SECRET_SHAPE_POLICY=enforce` without anyone editing the
  runtime's hard-coded registry
- A package `README.md` recording the actions table and the credential contract (which secret to
  set, and that nothing refreshes it)
- Success-flag envelopes: when a success schema declares `ok` as a required boolean, a 2xx body
  with `ok: false` is raised as a `BUSINESS` error instead of being returned as a successful
  call. Narrow by design — optional or non-boolean `ok` fields are left alone

### Testing & build gate

- Offline schema/contract tests only: parse the spec's `example` into the generated model when
  present, else synthesize one with `polyfactory`
- Generated tests ship with the connector (`packages/connectors/<id>/tests/`)
- Import smoke test + `pytest` on staged output is a hard gate — promote only happens on green
- Atomic two-phase promote into `src/node_wire_<id>/` and `packages/connectors/<id>/`; abort
  leaves the repo untouched

### Wiring & hand-off

- Optional `--wire`: upserts `config/connectors.yaml` (comment-preserving via `ruamel.yaml`) and
  appends secret placeholders / allowlist entries to `sample.env`
- Automatic hand-off to `nw-mcp-builder` after a clean promote (unless `--no-mcp`), producing an
  MCP host under `nw-mcp-builder/out/`

---

## Out of scope

### Auth schemes

- **OAuth2 / OpenID Connect acquisition for generated connectors** — Node Wire never *acquires*
  tokens for generated connectors, deliberately. Every `oauth2` flow and `openIdConnect` map to
  the host-supplied bearer tier above. Hand-written connectors that already use
  `OAuth2AuthProvider` are unchanged.
- **mutualTLS** — genuinely unpresentable (a transport-layer client certificate, not a
  header/param); operations secured only by it are soft-dropped
- **Cookie-based API keys** — no `name=value` cookie formatting in `StaticTokenAuthProvider` yet;
  soft-dropped
- **AND-combined** multi-scheme security (`security: [{a: [], b: []}]`) — soft-dropped
- Automatic acquisition of OAuth2 / OIDC credentials by generated connectors — Node Wire will
  present a host-supplied bearer token (see above) but will never obtain, refresh, or
  detect its expiry

(Host-supplied `oauth2` / `openIdConnect`, and multi-scheme connectors via per-action
`auth_schemes:`, are in scope above.)

### Spec features

- **Remote `$ref`s** (absolute URLs) — rejected outright; the input document must be
  self-contained aside from local relative/`#/` refs
- **Path- or operation-level `servers` overrides** — ignored; only the connector-level base URL
  is used (noted in the build report when present)
- Uncommon parameter serialization: `deepObject` query style, non-`simple` path/header styles,
  unsupported `collectionFormat` values (Swagger 2.0) — soft-dropped per operation
- `in: cookie` parameters — soft-dropped per operation

### Behavior not generated

- **Pagination** — no auto-pagination in v1, even when the spec documents cursor/offset
  patterns; generated actions return one page as-is
- **Rate-limit / observability metadata** — not derived from the spec (e.g. `x-ratelimit-*`
  extensions are ignored); deferred, not designed
- Complex/ambiguous request bodies and non-object success responses fall back to permissive
  typing (`Any` / `RestResponseOutput`) rather than a fully modeled schema

### Testing depth

- **Mock-server tests and live/integration tests against the real API are explicitly out of
  scope** — only offline schema/contract tests against generated models are produced
- No contract testing against the live upstream service as part of the generator's gate

### Registration & deployment

- `--wire` never edits `connector_registry.py` or the root `pyproject.toml` — entry-point
  registration for editable monorepo installs is a manual follow-up step
- Publishing (PyPI wheel `setup.py`/Cython glue, `scripts/build-packages.sh` allowlist entries,
  CI allowlists, standalone MCP Docker image rows) is a manual **Tier 2/3** checklist in
  [packaging.md](../packaging.md) — the builder produces the runtime + package skeleton only
- No deployment step: the builder stops at a promoted connector (+ optional MCP host); running
  `thv`/ToolHive deploy or verify is manual — see [nw-mcp-builder](nw-mcp-builder.md#platform-and-toolhive-read-this-first)
  and [toolhive_agent_scenario.md](../toolhive_agent_scenario.md). This was explicitly dropped from
  the companion `nw-cli` orchestrator's scope too; see [nw-cli.md](nw-cli.md).

### Editing generated output

- Generated files are marked "do not hand-edit" — there is no supported workflow for
  incrementally patching a generated connector; the only supported update path is regenerating
  with `--force` (full overwrite, not a diff/merge)

---

## Soft-drop vs. hard failure

Everything above that's "soft-dropped" means: the operation is skipped and listed in
`report.json`, but the build continues. The only way an out-of-scope feature aborts the build
is if it leaves **zero usable operations** — that's a hard failure (`DeriveError`), since a
connector with no actions isn't useful. A coverage warning is also printed when fewer than 50%
of the document's operations survive derivation, even if the build otherwise succeeds.

This soft-fail-and-report design is deliberate: the generator is meant to get you most of the
way there for typical REST APIs, with the report telling you exactly what to hand-write or
follow up on for the rest — not to silently produce a broken or partial connector.

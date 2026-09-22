<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder — scope

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
  `static_token` / `apikey_query` auth providers
- `oauth2` with a declared `clientCredentials` or `authorizationCode` flow — mapped to
  `OAuth2AuthProvider` (`grant_method: client_secret_post` / `refresh_token`). Neither flow is
  minted by the generator from spec data alone; it scaffolds what *is* derivable (token URL,
  declared scopes) and emits blank secret placeholders for what the operator must provision.
  `clientCredentials` needs only `<ID>_CLIENT_ID` / `<ID>_CLIENT_SECRET` (fully unattended).
  `authorizationCode` additionally needs `<ID>_REFRESH_TOKEN`, obtained via a one-time
  interactive consent completed **outside** Node Wire — see "OAuth2 authorizationCode" below.
- `oauth2` with no unattended flow (`implicit` / `password` / none declared) and `openIdConnect` —
  mapped to a **host-supplied** bearer (`static_token`, `host_supplied: true`): Node Wire presents
  `<ID>_ACCESS_TOKEN` verbatim as a `Bearer` header but never acquires, refreshes, or detects the
  expiry of it — see "Host-supplied auth tier" below. This is presentation only; the acquisition
  ban on `implicit` / `password` (below, under "Out of scope") is unchanged.
- Operations needing a **different, still-presentable** scheme than the connector default are not
  dropped — the generator emits it as an additional, named entry in `auth_schemes:` and routes just
  those actions to it (`auth_scheme=<name>` per `@nw_action`, resolved at runtime by
  `resolve_auth_provider`, which fails closed on an unknown name). A connector is no longer
  necessarily single-scheme; see "Per-action auth schemes" below.
- Anonymous connectors (no scheme, or only unsupported schemes present) build as `auth: none`

#### Host-supplied auth tier

Some schemes Node Wire can *present* but will never *acquire*: `oauth2` `implicit` / `password` (no
refresh token, or a flow that needs a raw user password — ruled out on principle, not tooling, see
"Out of scope" below) and `openIdConnect` (its underlying flow can't be introspected from the spec
alone). Rather than soft-dropping every operation gated by one of these — which is what silently
turned "13/20 operations generated" into "20/20" for petstore-style specs — the generator scaffolds
a `static_token` provider marked `host_supplied: true` that presents `<ID>_ACCESS_TOKEN` as a
`Bearer` header with **no** acquisition, refresh, or expiry detection. The host application owns
obtaining and rotating that token entirely out-of-band; a stale token surfaces as a plain `401`
from the upstream API, not a managed refresh cycle. The build report's `auth.notes` names the
triggering scheme and the exact secret key to set.

**This is not OAuth2 (or OIDC) client support — it's a static bearer, full stop.** At runtime,
`host_supplied: true` is inert: the provider is the exact same `StaticTokenAuthProvider` used for
a plain `http: bearer` scheme, with the flag existing only for the build report / generated
`sample.env` comment. Node Wire never calls a token endpoint, never performs a grant exchange,
never negotiates scope, never authenticates as a client — none of the actual OAuth2 protocol runs
for these schemes. Contrast this with `clientCredentials` / `authorizationCode` above, where Node
Wire genuinely *is* an OAuth2 client: it owns the grant exchange and refreshes the token
indefinitely, unattended, after one-time setup. "We'll present any bearer token you hand us" is a
much weaker claim than "we support this flow," and deliberately so — see the acquisition-vs-
presentation distinction above.

This also means the operational cost is real, not just a labeling nuance. `implicit` tokens in
particular are typically short-lived (commonly ~1 hour) with **no refresh token by spec** — so a
host using this tier for an `implicit`-secured operation has to redo the *entire interactive
browser redirect* every time the token expires and update `<ID>_ACCESS_TOKEN` itself. Nothing in
Node Wire detects the coming expiry or warns beforehand; the connector just starts getting `401`s.
That is a materially heavier operational burden than the flows Node Wire actually manages, where
expiry and renewal are invisible to the operator after initial setup.

#### Per-action auth schemes

A connector can serve **more than one** OpenAPI security scheme from a single instance. The
connector-level default (`auth:` in `connectors.yaml`) still covers most operations; any operation
whose declared security is a *different* scheme — but one that's still presentable (self-managed or
host-supplied, i.e. not `mutualTLS`, cookie `apiKey`, AND-multi, or unrecognized) — gets its own
named entry under the new, additive `auth_schemes:` config block, and the generated action passes
`auth_scheme=<name>` so the runtime resolves the right `AuthProvider` per call instead of per
connector. This is what let petstore-style specs (an `apiKey` default alongside operations gated by
an `oauth2` `implicit` scheme) stop soft-dropping those operations.

#### OAuth2 `authorizationCode`

This flow requires a human to complete a browser redirect + consent at least once — no
generator can (or should) do that for an arbitrary host app. What the generator produces
instead is a `grant_method: refresh_token` scaffold: the *first* refresh token is a manual,
out-of-band step (register an app with the provider, complete the interactive consent, copy
the resulting refresh token into `<ID>_REFRESH_TOKEN`); every access token after that is minted
automatically by `OAuth2AuthProvider` from the stored refresh token, no further interaction
needed. The report's `auth.notes` entry spells out the authorization URL and the exact env vars
to set.

**`offline_access`:** most OIDC providers, including Microsoft identity platform, only issue a
refresh token during that interactive consent if `offline_access` was among the requested
scopes — a resource API's OpenAPI spec commonly omits this protocol-level scope from its
resource-specific scope list (it's not one of *its* permissions). The generator adds
`offline_access` to the derived `scopes` automatically when it's missing, so the scope list
you copy for the manual consent step is one that will actually produce a refresh token.

**Rotation:** if the identity provider rotates the refresh token on use (Entra does this
routinely), `OAuth2AuthProvider` caches the new value in memory and keeps working for the rest
of that process's lifetime even with no persistence configured. Pass
`OAuth2AuthProvider(on_refresh_token_rotated=...)` (wired automatically for `--wire`-generated,
YAML-configured connectors — see `_build_auth_provider` in `src/bindings/factory.py`, which
persists into the process-wide secret overlay) so the replacement also survives a restart.
Node Wire's own persistence is process-local only — see [`mcp-client-oauth.md`](mcp-client-oauth.md)
for the analogous host-owns-durable-persistence pattern used for outbound MCP client auth.

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

- **OAuth2 `implicit` and `password` (ROPC)** — Node Wire never *acquires* these, deliberately.
  `implicit` is deprecated and has no refresh token; `password` requires the connector to handle a
  raw user password, which this codebase's secrets-hygiene posture rules out on principle, not
  just for lack of tooling. Operations secured only by these are no longer soft-dropped, though —
  see "Host-supplied auth tier" above, which presents (but never obtains) a host-supplied bearer
  token instead.
- **mutualTLS** — genuinely unpresentable (a transport-layer client certificate, not a
  header/param); operations secured only by it are soft-dropped
- **Cookie-based API keys** — no `name=value` cookie formatting in `StaticTokenAuthProvider` yet;
  soft-dropped
- **AND-combined** multi-scheme security (`security: [{a: [], b: []}]`) — soft-dropped
- Automatic acquisition of `implicit` / `password` / `openIdConnect` credentials — Node Wire will
  present a host-supplied bearer token for these (see above) but will never obtain, refresh, or
  detect its expiry

(`oauth2` with `clientCredentials` or `authorizationCode` flows, `openIdConnect` and
non-unattended `oauth2` flows via the host-supplied tier, and multi-scheme connectors via
per-action `auth_schemes:`, all moved to "In scope" above.)

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
  [packaging.md](packaging.md) — the builder produces the runtime + package skeleton only
- No deployment step: the builder stops at a promoted connector (+ optional MCP host); running
  `thv`/ToolHive deploy or verify is manual, per `scripts/deploy-openapi-mcp-toolhive.md` — this
  was explicitly dropped from the companion `nw-cli` orchestrator's scope too, see
  [nw-cli.md](nw-cli.md)

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

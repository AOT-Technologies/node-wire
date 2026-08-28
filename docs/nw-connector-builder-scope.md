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

- One connector-level scheme, chosen from the document's top-level `security` (or the most
  common scheme across operations if none is declared)
- `apiKey` in `header` or `query`, `http` `bearer`, `http` `basic` — mapped to Node Wire's
  `static_token` / `apikey_query` auth providers
- Anonymous connectors (no scheme, or only unsupported schemes present) build as `auth: none`

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

- **OAuth2** and **OpenID Connect** — operations secured only by these are soft-dropped, not
  supported with a workaround
- **mutualTLS**
- **Cookie-based API keys**
- **AND-combined** multi-scheme security (`security: [{a: [], b: []}]`)
- More than one auth provider per connector — a connector is single-scheme, even if the spec
  declares several; operations needing a divergent scheme are soft-dropped
- Per-operation auth overrides — auth is connector-level only

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

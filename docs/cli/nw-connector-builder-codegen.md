<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder — codegen behaviour

Why generated connectors look the way they do. You do not need this page to *run*
[`nw-connector-builder`](nw-connector-builder.md) or [`nw gen-all`](nw-cli.md) — read it when
generated output surprises you: an argument you expected is missing, a description is
truncated, a field was renamed, or a `200 OK` came back as an error.

For what the generator supports at all, see
[generator contract](nw-connector-builder-scope.md).

---

## Spec repair

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

## Related docs

| Doc | When to read it |
|-----|-----------------|
| [nw-connector-builder.md](nw-connector-builder.md) | Running the generator |
| [nw-connector-builder-scope.md](nw-connector-builder-scope.md) | What is in and out of scope |
| [Build a connector](../connectors-build.md) | Hand-written connectors |

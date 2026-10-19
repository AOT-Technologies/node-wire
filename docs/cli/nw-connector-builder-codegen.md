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

Only schemas are repaired. Values under `example`, `examples`, `default`, `enum`, `const` and `x-` extensions are data, so a payload such as GitHub's search example `{"items": ["…"]}` is left as it is. Property names are not data keys: a property called `example` or `default` is still a schema and is still repaired.

Swagger 2.0 input gets one more repair during conversion: response `examples` (keyed by mime type on the response object, which OAS 3 has no property for) moves to `content.<mime>.example`.

### Recursive schemas

Generated models are built from a fully inlined document, and inlining never ends on a `$ref`
cycle. Large real-world specs have cycles. Microsoft Graph's `chatMessage.replies` is a list of
`chatMessage`, and `team` and `channel` point at each other. So the loader cuts each cycle where it
closes. The `$ref` that would re-enter a schema already being expanded becomes:

```yaml
type: object
additionalProperties: true
x-recursive-ref: <the $ref that was cut>
```

On that field, the generated model accepts any object, one level below the last fully typed one.
Here, `replies` becomes a list of free-form objects. Top-level tool arguments keep their types.
The build logs the cut schemas by name (`Cut recursive $ref cycles at N schema(s) …`). Specs
without cycles are unaffected.

### Invalid operations

A spec that fails validation is not rejected outright. Each operation and each component is validated on its own, the invalid ones are removed, and the rest is validated again. Removed operations are soft-dropped with `invalid OpenAPI: <reason>`; removed components are named in the report's `notes`. Twitch's community spec, for example, lists `type` twice in one `required` list: only `get-hype-train-status` is dropped, and the other 148 operations build.

### Request examples

The generated tests parse each operation's request example into its input model. An example that does not match its own schema (GitHub's `markdown/render-raw` shows a JSON object for a `text/plain` body) is not used as a test, so a documentation mistake in the spec cannot fail the build gate. Those operations are named in the report's `notes`.



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
- **Name clashes** — a path/query/header parameter that shares a name with a body property is renamed `<name>__<location>` (e.g. `channel__query`); the body property keeps the plain name. Names the bindings reserve (`action`, `body`, `config_name`, `tenant_id`, `trace_id`) get a `_param` suffix (a field renamed from `action` is never aliased back to `action`, which is the input model's dispatch discriminator), Python keywords a trailing `_` (`from` → `from_`, which also accepts `from`). The value is always sent under its original wire name.
- **Whole bodies** — bodies that are not objects with declared properties keep a single `body` argument: arrays (`list`), binary/string bodies (`str`), free-form maps, `oneOf`/`anyOf`, and media types other than JSON, form and multipart. `body` is required when the request body is.

---

## Parameter serialization

Every parameter keeps its OpenAPI `in`, `style` and `explode` in the field's metadata
(`nw_in`, `nw_style`, `nw_explode`), and `node_wire_runtime.rest` serializes it as the spec
defines:

| Location | Styles | Notes |
|---|---|---|
| `path` | `simple`, `label`, `matrix` | Values percent-encoded, separators (`,` `.` `;` `=`) kept |
| `query` | `form`, `spaceDelimited`, `pipeDelimited`, `deepObject` | `allowReserved: true` sends reserved characters as-is (`nw_allow_reserved`) |
| `header` | `simple` | Objects as `k,v` or, exploded, `k=v` |
| `cookie` | `form` | One `Cookie` header, values percent-encoded |
| `querystring` (3.2) | — | The whole query string, from an object (form-encoded) or a string |

Booleans are sent as `true` / `false`, nested values as compact JSON. A parameter declared with
`content` instead of `schema` is sent as that media type's text, e.g. JSON (`nw_media_type`).

`Accept`, `Content-Type` and `Authorization` header parameters are ignored, as OpenAPI requires:
the body media type and auth set those. An operation parameter overrides a path-level one with
the same name and location. An optional parameter the connector cannot send (Swagger 2.0
`collectionFormat: tsv`) is left out and named in the report's `notes`; a required one soft-drops
the operation.

## Schema constraints

Constraints become Pydantic field constraints, so every binding rejects a bad value before the
call: `minimum` / `maximum` (with 3.0's boolean and 3.1's numeric `exclusiveMinimum` /
`exclusiveMaximum`), `multipleOf`, `minLength` / `maxLength`, `minItems` / `maxItems`, and `enum`
or `const` as a `Literal` (string, integer or boolean values; `null` in an enum is dropped).
`pattern` and `format` are advertised in the tool schema but not enforced: spec patterns are
ECMA-262 regular expressions, which Python does not always read the same way, and the upstream API
validates anyway.

## Request bodies by media type

| Media type | Argument | Sent as |
|---|---|---|
| JSON, `+json` | one per property (object bodies), else `body` | JSON |
| `application/x-www-form-urlencoded` | one per property | form fields; arrays of scalars as repeated keys, objects as JSON text |
| `multipart/*` | one per property | multipart parts, always (never downgraded to urlencoded); binary properties as file parts with the Encoding Object's `contentType` |
| `text/*`, XML, other | `body: str` | the string as-is |
| binary (`format: binary` / `byte`; 3.1+ `contentEncoding: base64` or a non-text `contentMediaType`), `application/octet-stream` | `body: str` (base64) | decoded bytes |

A media type with no `schema` still takes a free-form body. Only one media type per operation is
used: JSON first, otherwise the first listed.

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

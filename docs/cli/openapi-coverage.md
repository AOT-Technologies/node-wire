<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# OpenAPI coverage

This page lists every object and field of Swagger 2.0 (2014-09-08), OpenAPI 3.0.4 (2024-10-24),
OpenAPI 3.1.2 (2025-09-19) and OpenAPI 3.2.1 (2026-09-10), and says what `nw-connector-builder`
does with each one. Earlier patch releases of each line (3.0.0–3.0.3, 3.1.0–3.1.1, 3.2.0) have the
same fields and are handled the same way. Swagger 2.0 documents are converted to OpenAPI 3.0 first,
so their rows say how each 2.0 construct is converted. The **Status** column uses the five values in
the legend below, and the last column says what happens or why it does not. The rules behind these
rows are in the [generator contract](nw-connector-builder-scope.md), and the reasons for specific
generated shapes are in [codegen behaviour](nw-connector-builder-codegen.md). The conformance
fixtures in `tests/nw_connector_builder/fixtures/coverage/` (`swagger20.yaml`, `oas30.yaml`,
`oas31.yaml`, `oas32.yaml`) hold one operation per covered construct, and
`tests/nw_connector_builder/test_openapi_coverage.py` builds each one and checks the HTTP request
the generated connector sends.

## Legend

| Status | Meaning |
|---|---|
| **Covered** | Read and applied: the generated connector sends what the spec describes. |
| **Converted** | Swagger 2.0 construct, mapped to its OpenAPI 3 equivalent and then handled like that. |
| **Partial** | Some of it is applied; the row says which part is not. |
| **Not needed** | Has no effect on an outbound API call (documentation, presentation, or inbound-only metadata). Ignored. |
| **Skipped** | Would affect a call but is not supported; the row gives the reason. Listed again under [Not covered, and why](#not-covered-and-why). |

Versions are written `2.0`, `3.0`, `3.1`, `3.2`; `3.0+` means 3.0, 3.1 and 3.2.

## OpenAPI Object / Swagger Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `swagger` | 2.0 | Converted | `"2.0"` selects the in-house converter, which outputs an OpenAPI 3.0.3 document. |
| `openapi` | 3.0+ | Covered | Any `3.x` value is accepted; `3.0.*` selects the 3.0 example validator, later versions the 3.1 one. |
| `$self` | 3.2 | Skipped | Not read. Relative `$ref`s resolve against the file location or fetch URL instead. |
| `info` | 2.0, 3.0+ | Not needed | Documentation only. |
| `jsonSchemaDialect` | 3.1+ | Skipped | Not read. Schemas are always read with the default OAS dialect. |
| `host` | 2.0 | Converted | Becomes the server URL. A missing `host` uses the host the spec was fetched from. For a local file, pass `--base-url`. |
| `basePath` | 2.0 | Converted | Appended to the server URL. |
| `schemes` | 2.0 | Converted | `https` is preferred when listed, else the first entry. A missing `schemes` uses the fetch URL's scheme, else `https`. |
| `consumes` | 2.0 | Converted | Picks the request media type: JSON first, else the first entry. Operation-level `consumes` wins. |
| `produces` | 2.0 | Converted | Picks the response media type the same way. |
| `servers` | 3.0+ | Covered | The first entry is the connector's base URL. Later entries are alternatives an operator can pick with `base_url` in `connectors.yaml`. `--base-url` overrides at build time. |
| `paths` | 2.0, 3.0+ | Covered | Every operation is generated or soft-dropped with a reason. |
| `webhooks` | 3.1+ | Skipped | Requests the API sends to you, not calls a connector makes. The count is noted in the build report. |
| `components` | 3.0+ | Covered | Resolved wherever a `$ref` points into it. See [Components Object](#components-object-and-20-definition-containers). |
| `definitions` | 2.0 | Converted | Moved to `components.schemas`; `#/definitions/` refs are rewritten. |
| `parameters` | 2.0 | Converted | Each `#/parameters/` ref is replaced with a copy of its target before conversion, so reusable `body` and `formData` parameters work. |
| `responses` | 2.0 | Converted | Each `#/responses/` ref is replaced with a copy of its target before conversion. |
| `securityDefinitions` | 2.0 | Converted | Moved to `components.securitySchemes`. See [Security Scheme Object](#security-scheme-object). |
| `security` | 2.0, 3.0+ | Covered | The default requirement for every operation. See [Security Requirement Object](#security-requirement-object). |
| `tags` | 2.0, 3.0+ | Not needed | Grouping for documentation. Not used for action names or descriptions. |
| `externalDocs` | 2.0, 3.0+ | Not needed | Documentation only. |
| `^x-` | 2.0, 3.0+ | Not needed | Every `x-` extension is removed before `$ref` resolution. The generator reads none of them, and removing them stops a dangling `$ref` inside an unused extension from failing the build. |

## Info Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `title` | 2.0, 3.0+ | Not needed | Documentation only. |
| `summary` | 3.1+ | Not needed | Documentation only. |
| `description` | 2.0, 3.0+ | Not needed | Documentation only. |
| `termsOfService` | 2.0, 3.0+ | Not needed | Documentation only. |
| `contact` | 2.0, 3.0+ | Not needed | Documentation only. |
| `license` | 2.0, 3.0+ | Not needed | Documentation only. |
| `version` | 2.0, 3.0+ | Not needed | The API document's version. The generated package has its own version. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Contact Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `name` | 2.0, 3.0+ | Not needed | Documentation only. |
| `url` | 2.0, 3.0+ | Not needed | Documentation only. |
| `email` | 2.0, 3.0+ | Not needed | Documentation only. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## License Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `name` | 2.0, 3.0+ | Not needed | The API's license, not the generated package's (always Apache-2.0). |
| `identifier` | 3.1+ | Not needed | As `name`. |
| `url` | 2.0, 3.0+ | Not needed | As `name`. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Server Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `url` | 3.0+ | Covered | The base URL. When the spec was fetched over HTTP(S), a relative URL (or no `servers` at all) is resolved against the fetch URL. For a local file it fails the build with a request for `--base-url`. |
| `description` | 3.0+ | Not needed | Documentation only. |
| `name` | 3.2 | Not needed | A label for the server. |
| `variables` | 3.0+ | Covered | Each `{name}` is replaced with its variable's `default`. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Server Variable Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `enum` | 3.0+ | Skipped | The `default` is always used. To use another value, set `base_url` in `connectors.yaml` or pass `--base-url`. |
| `default` | 3.0+ | Covered | Substituted into the URL. A variable with no default fails the build for the root server; for an operation server, see [Path Item Object](#path-item-object). |
| `description` | 3.0+ | Not needed | Documentation only. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Components Object and 2.0 definition containers

Components are used through `$ref`: every local reference is inlined before code generation. A
component that fails validation on its own is removed and named in the report's `notes`. Empty
structural fields (an empty YAML key such as `parameters:`) are removed before validation; data
nulls such as `default: null`, `example: null` or a `null` enum member are kept. If the validator
itself fails on one operation or component, that part is treated as invalid and dropped with the
reason instead of failing the build.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `schemas` | 3.0+ | Covered | Inlined wherever referenced. |
| `responses` | 3.0+ | Covered | Inlined wherever referenced. |
| `parameters` | 3.0+ | Covered | Inlined wherever referenced. |
| `examples` | 3.0+ | Covered | Inlined wherever referenced. See [Example Object](#example-object). |
| `requestBodies` | 3.0+ | Covered | Inlined wherever referenced. |
| `headers` | 3.0+ | Not needed | Inlined, but Header Objects have no effect on a call. See [Header Object](#header-object). |
| `securitySchemes` | 3.0+ | Covered | Read directly. See [Security Scheme Object](#security-scheme-object). |
| `links` | 3.0+ | Not needed | See [Link Object](#link-object). |
| `callbacks` | 3.0+ | Skipped | See [Callback Object](#callback-object). |
| `pathItems` | 3.1+ | Covered | Inlined where a path's `$ref` points at one. |
| `mediaTypes` | 3.2 | Covered | Inlined where a `content` entry's `$ref` points at one. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |
| Definitions Object `{name}` | 2.0 | Converted | Becomes `components.schemas.{name}`. |
| Parameters Definitions Object `{name}` | 2.0 | Converted | Inlined at each use, then converted like an operation parameter (a `body` or `formData` one becomes a request body). |
| Responses Definitions Object `{name}` | 2.0 | Converted | Inlined at each use, then converted like an operation response. |
| Security Definitions Object `{name}` | 2.0 | Converted | Becomes `components.securitySchemes.{name}`. |

## Paths Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `/{path}` | 2.0, 3.0+ | Covered | Appended to the base URL. Each `{name}` is filled from its path parameter. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Path Item Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `$ref` | 2.0, 3.0+ | Partial | 3.x: resolved, but the referenced Path Item replaces the local one, so operations written next to the `$ref` are lost. 2.0: a Path Item in another file is not converted to 3.0, so its operations fail validation and are soft-dropped. |
| `summary` | 3.0+ | Not needed | Tool descriptions come from the operation. |
| `description` | 3.0+ | Not needed | Tool descriptions come from the operation. |
| `get` | 2.0, 3.0+ | Covered | One action per operation. |
| `put` | 2.0, 3.0+ | Covered | One action per operation. |
| `post` | 2.0, 3.0+ | Covered | One action per operation. |
| `delete` | 2.0, 3.0+ | Covered | One action per operation. |
| `options` | 2.0, 3.0+ | Covered | One action per operation. |
| `head` | 2.0, 3.0+ | Covered | One action per operation. |
| `patch` | 2.0, 3.0+ | Covered | One action per operation. |
| `trace` | 3.0+ | Covered | One action per operation. |
| `query` | 3.2 | Covered | Sent as the HTTP `QUERY` method. |
| `additionalOperations` | 3.2 | Covered | One action per entry, sent with the method name in upper case (for example `PURGE`). |
| `servers` | 3.0+ | Covered | Used for each operation on the path that has no `servers` of its own. See the Operation `servers` row. |
| `parameters` | 2.0, 3.0+ | Covered | Merged into each operation; an operation parameter with the same `name` and `in` overrides it. The 2.0 converter does the same merge, so a redefined parameter appears once. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Operation Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `tags` | 2.0, 3.0+ | Not needed | Grouping for documentation. |
| `summary` | 2.0, 3.0+ | Covered | The tool description: first paragraph, Markdown links flattened, capped at 300 characters. |
| `description` | 2.0, 3.0+ | Covered | Used as the tool description when there is no `summary`. |
| `externalDocs` | 2.0, 3.0+ | Not needed | Documentation only. |
| `operationId` | 2.0, 3.0+ | Covered | Becomes the action name in snake case, made unique and kept within a length budget. Without one, the name is built from the method and path. |
| `consumes` | 2.0 | Converted | Overrides root `consumes` when choosing the request media type. An empty list falls back to the root value instead of clearing it. |
| `produces` | 2.0 | Converted | Overrides root `produces` when choosing the response media type. |
| `parameters` | 2.0, 3.0+ | Covered | See [Parameter Object](#parameter-object). |
| `requestBody` | 3.0+ | Covered | See [Request Body Object](#request-body-object). In a 3.0 document a request body on `GET`, `HEAD` or `DELETE` is ignored, as 3.0 says it is not supported there; 3.1+ documents keep it. |
| `responses` | 2.0, 3.0+ | Covered | See [Responses Object](#responses-object). |
| `callbacks` | 3.0+ | Skipped | See [Callback Object](#callback-object). |
| `deprecated` | 2.0, 3.0+ | Covered | The action is marked `@nw_action(..., deprecated=True)`. |
| `security` | 2.0, 3.0+ | Covered | Overrides the root `security`; `[]` makes the action anonymous. See [Security Requirement Object](#security-requirement-object). |
| `servers` | 3.0+ | Covered | The first entry, with variables at their defaults, is where this action is called when it differs from the base URL; the report lists these actions. `base_url` in `connectors.yaml` does not change it. A relative URL or a variable with no default is ignored with a note, and the base URL is used. |
| `schemes` | 2.0 | Converted | Removed; OpenAPI 3 has no operation-level equivalent. The server's scheme applies (`https` preferred, see the root `schemes` row). |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## External Documentation Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `description` | 2.0, 3.0+ | Not needed | Documentation only. |
| `url` | 2.0, 3.0+ | Not needed | Documentation only. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Parameter Object

Each parameter becomes one argument of the action, carrying its `in`, `style` and `explode` in the
field metadata so the runtime can serialize it (see
[parameter serialization](nw-connector-builder-codegen.md#parameter-serialization)).

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `name` | 2.0, 3.0+ | Covered | The wire name. The argument name is a Python-safe version of it. Header parameters named `Accept`, `Content-Type` or `Authorization` are ignored as the spec requires, and the report notes them. |
| `in` | 2.0, 3.0+ | Covered | See [Parameter `in` values](#parameter-in-values). |
| `description` | 2.0, 3.0+ | Covered | The argument description: first sentence, capped at 90 characters. The schema's `description` is used when the parameter has none. |
| `required` | 2.0, 3.0+ | Covered | Required arguments. Path parameters are always required. |
| `deprecated` | 3.0+ | Not needed | The parameter is still offered and sent. |
| `allowEmptyValue` | 2.0, 3.0+ | Partial | An empty string is sent as `name=`. A bare `name` with no `=` is never produced. Deprecated in 3.2. |
| `style` | 3.0+ | Covered | See [Style values](#style-values). An unsupported style leaves out an optional parameter (named in the report) and soft-drops the operation when the parameter is required. |
| `explode` | 3.0+ | Covered | Applied per style; the default is `true` for `form` and `false` otherwise. |
| `allowReserved` | 3.0+ | Partial | Honored for `in: query` and `in: path`: RFC 3986 reserved characters are sent unencoded, except `&`, `=` and `#`. Not applied to `cookie` parameters. |
| `schema` | 2.0, 3.0+ | Covered | Gives the argument's type and constraints. See [Schema Object](#schema-object). |
| `content` | 3.0+ | Covered | The single media type's schema types the argument. A JSON media type is sent as compact JSON text; other media types send the value as given. |
| `example` | 3.0+ | Not needed | Parameter examples are not used for generated tests. |
| `examples` | 3.0+ | Not needed | As `example`. |
| `type` | 2.0 | Converted | Moved into `schema`. |
| `format` | 2.0 | Converted | Moved into `schema`. |
| `items` | 2.0 | Converted | Moved into `schema`. See [Items Object](#items-object). |
| `collectionFormat` | 2.0 | Converted | Mapped to `style` and `explode`. See [collectionFormat mapping](#20-collectionformat-mapping). |
| `default` | 2.0 | Converted | Moved into `schema`. |
| `maximum` | 2.0 | Converted | Moved into `schema`. |
| `exclusiveMaximum` | 2.0 | Converted | Moved into `schema`. |
| `minimum` | 2.0 | Converted | Moved into `schema`. |
| `exclusiveMinimum` | 2.0 | Converted | Moved into `schema`. |
| `maxLength` | 2.0 | Converted | Moved into `schema`. |
| `minLength` | 2.0 | Converted | Moved into `schema`. |
| `pattern` | 2.0 | Converted | Moved into `schema`. |
| `maxItems` | 2.0 | Converted | Moved into `schema`. |
| `minItems` | 2.0 | Converted | Moved into `schema`. |
| `uniqueItems` | 2.0 | Converted | Moved into `schema`. |
| `enum` | 2.0 | Converted | Moved into `schema`. |
| `multipleOf` | 2.0 | Converted | Moved into `schema`. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

### Parameter `in` values

| Value | Versions | Status | How it is handled / why |
|---|---|---|---|
| `path` | 2.0, 3.0+ | Covered | Substituted into the path, percent-encoded (including `/`, unless `allowReserved` is set). |
| `query` | 2.0, 3.0+ | Covered | Added to the query string. |
| `header` | 2.0, 3.0+ | Covered | Sent as a request header. Operator `--header` values and the auth provider's headers win over a caller's value. |
| `cookie` | 3.0+ | Covered | Collected into one `Cookie` header. `form` values are percent-encoded; 3.2 `style: cookie` values are sent as given. |
| `querystring` | 3.2 | Covered | One argument holding the whole query string. An object under a non-JSON media type is form-encoded, JSON is sent as encoded JSON text, and a string is sent as given. |
| `formData` | 2.0 | Converted | All `formData` parameters become one object request body: `multipart/form-data` when one is `type: file` or `consumes` lists multipart, else `application/x-www-form-urlencoded`. |
| `body` | 2.0 | Converted | Becomes `requestBody`, with the media type from `consumes` (JSON first). `required` carries over. |

### Style values

| Style | Versions | Status | How it is handled / why |
|---|---|---|---|
| `matrix` | 3.0+ | Covered | `;name=value` path segments, exploded or not. |
| `label` | 3.0+ | Covered | `.value` path segments, exploded or not. |
| `simple` | 3.0+ | Covered | Path and header values; objects as `k,v` or, exploded, `k=v`. |
| `form` | 3.0+ | Covered | Query: arrays as repeated keys or comma-joined, objects as `k=v` pairs or comma-joined. Cookie: values percent-encoded; with `explode` (the default), arrays and objects become separate `name=value` pairs joined by `; `, otherwise comma-joined. |
| `spaceDelimited` | 3.0+ | Covered | Query arrays joined with spaces. |
| `pipeDelimited` | 3.0+ | Covered | Query arrays joined with `\|`. |
| `deepObject` | 3.0+ | Covered | Query objects as `name[key]=value`. |
| `cookie` | 3.2 | Covered | Cookie values sent as given, without percent-encoding; arrays and objects as `style: form`. |

### 2.0 collectionFormat mapping

| collectionFormat | Versions | Status | How it is handled / why |
|---|---|---|---|
| `csv` | 2.0 | Converted | `style: simple` in path and header, `style: form, explode: false` in query. On `formData`, arrays are sent as repeated keys instead. |
| `ssv` | 2.0 | Converted | `style: spaceDelimited` in query. Ignored on `formData` (repeated keys). |
| `tsv` | 2.0 | Skipped | OpenAPI 3 has no equivalent. An optional parameter is left out and noted; a required one soft-drops the operation. Ignored on `formData` (repeated keys). |
| `pipes` | 2.0 | Converted | `style: pipeDelimited` in query. Ignored on `formData` (repeated keys). |
| `multi` | 2.0 | Converted | `style: form, explode: true` in query; repeated keys on `formData`. |

## Items Object

2.0 only. A parameter's `items` is moved into its `schema.items`, so these fields are then handled
as [Schema Object](#schema-object) keywords on the array items.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `type` | 2.0 | Converted | Types the array items (for example `list[int]`). |
| `format` | 2.0 | Converted | Kept on the item schema. |
| `items` | 2.0 | Converted | Nested arrays are kept, typed `list[Any]` below the first level. |
| `collectionFormat` | 2.0 | Converted | Removed; OpenAPI 3 has no place for it. The outer parameter's style still applies. |
| `default` | 2.0 | Converted | Kept on the item schema. |
| `maximum` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `exclusiveMaximum` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `minimum` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `exclusiveMinimum` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `maxLength` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `minLength` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `pattern` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `maxItems` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `minItems` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `uniqueItems` | 2.0 | Converted | Kept on the item schema; not enforced. |
| `enum` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `multipleOf` | 2.0 | Converted | Kept on the item schema; not enforced per item. |
| `^x-` | 2.0 | Not needed | Removed before resolution. |

## Request Body Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `description` | 3.0+ | Not needed | Not shown to the caller; each body property carries its own description. |
| `content` | 3.0+ | Covered | One media type is used: `application/json` first, then any JSON-like type, otherwise the first one listed. How each media type becomes arguments is in [request bodies by media type](nw-connector-builder-codegen.md#request-bodies-by-media-type). |
| `required` | 3.0+ | Covered | Makes the whole `body` argument required, or, for a flattened body, the properties the schema lists in `required`. An optional body makes every property optional. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Media Type Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `schema` | 3.0+ | Covered | Request: a JSON, form or multipart object with declared properties becomes one argument per property; anything else becomes a single `body` argument (`str` for text, XML and binary media). A media type without a schema takes a free-form body. Responses: see [Response Object](#response-object). |
| `itemSchema` | 3.2 | Skipped | Sequential and streaming media are not supported. A response is read whole and returned as text. A request with only `itemSchema` gets a free-form `body` that is not serialized as a sequence. Not planned. |
| `example` | 3.0+ | Covered | On a request body: becomes a generated test of the input model when it validates against its own schema (3.0 rules for 3.0 documents, 3.1 rules otherwise). Ignored on responses and parameters. |
| `examples` | 3.0+ | Covered | As `example`, using the first entry's `value` or 3.2 `dataValue`. |
| `encoding` | 3.0+ | Partial | Only `contentType` is used, for binary parts of a multipart body. See [Encoding Object](#encoding-object). |
| `prefixEncoding` | 3.2 | Skipped | Positional multipart encoding for sequential media; streaming is not supported. |
| `itemEncoding` | 3.2 | Skipped | Per-item multipart encoding for sequential media; streaming is not supported. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Encoding Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `contentType` | 3.0+ | Partial | Sent as the part's `Content-Type` for a binary (`format: binary` / `byte` or base64) multipart property. Other parts use the default: text, with objects as JSON text. |
| `headers` | 3.0+ | Skipped | Per-part multipart headers are not sent. Candidate for later. |
| `encoding` | 3.2 | Skipped | Nested encoding for nested multipart. Not supported. |
| `prefixEncoding` | 3.2 | Skipped | Nested positional encoding. Not supported. |
| `itemEncoding` | 3.2 | Skipped | Nested per-item encoding. Not supported. |
| `style` | 3.0+ | Skipped | Form fields always use `form` with `explode: true`: arrays of scalars as repeated keys, objects and nested arrays as JSON text. Candidate for later. |
| `explode` | 3.0+ | Skipped | As `style`. |
| `allowReserved` | 3.0+ | Skipped | Form values are always encoded. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Responses Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `default` | 2.0, 3.0+ | Covered | Used as the success response only when no `2xx` or `2XX` response is declared. |
| `{HTTP status code}` | 2.0, 3.0+ | Covered | The success response is the lowest explicit `2xx` code, then `2XX`. Any `4xx` or `5xx` reply raises an HTTP status error regardless of what is declared. A `3xx` reply is returned as the result, not followed. `1XX`–`5XX` ranges other than `2XX` are not read. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Response Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `summary` | 3.2 | Not needed | Documentation only. |
| `description` | 2.0, 3.0+ | Not needed | Documentation only. Optional in 3.2, which the generator accepts. |
| `schema` | 2.0 | Converted | Becomes `content.<produces type>.schema`. A `type: file` schema becomes `type: string, format: binary`. |
| `headers` | 2.0, 3.0+ | Not needed | Declared headers are not used. Enveloped results return every response header as received. 2.0 headers are converted to 3.0 form. |
| `examples` | 2.0 | Converted | Moved to `content.<mime>.example`. Response examples are not used afterwards. |
| `content` | 3.0+ | Partial | JSON object schema: the action returns the response's JSON object as is (a model that accepts any fields; output fields are not typed). Anything else (array, scalar, text, binary, no content) is returned in an envelope with `status_code`, `headers` and `body`. A non-JSON media type with an object schema (XML, say) is also returned in the envelope, with the body as text. A required boolean `ok` property turns a `2xx` body with `ok: false` into a business error. |
| `links` | 3.0+ | Not needed | See [Link Object](#link-object). |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Headers Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{name}` | 2.0 | Converted | Each header is converted to a 3.0 Header Object (see [Header Object](#header-object)). |

## Callback Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{expression}` | 3.0+ | Skipped | A callback is a request the API sends to you, not a call a connector makes. The report notes how many operations have callbacks. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Example Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{mime type}` | 2.0 | Converted | The 2.0 response example map moves to `content.<mime>.example`. |
| `summary` | 3.0+ | Not needed | Documentation only. |
| `description` | 3.0+ | Not needed | Documentation only. |
| `dataValue` | 3.2 | Covered | Used like `value` for request-body example tests. |
| `serializedValue` | 3.2 | Skipped | Not used for tests: turning serialized text back into input data would need a parser per media type. No effect on calls. |
| `value` | 3.0+ | Covered | The first request-body example becomes a generated test when it matches its schema. Mismatched examples are skipped and named in the report. |
| `externalValue` | 3.0+ | Skipped | Not fetched: the generator makes no network calls for referenced content (the same policy as remote `$ref`s). No effect on calls. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Link Object

Links describe how one response feeds the next call. The agent calling the tools chains them
itself, so none of these fields is used.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `operationRef` | 3.0+ | Not needed | Navigation hint for clients. |
| `operationId` | 3.0+ | Not needed | Navigation hint for clients. |
| `parameters` | 3.0+ | Not needed | Navigation hint for clients. |
| `requestBody` | 3.0+ | Not needed | Navigation hint for clients. |
| `description` | 3.0+ | Not needed | Documentation only. |
| `server` | 3.0+ | Not needed | Navigation hint for clients. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Header Object

Header Objects describe response headers and multipart part headers. Response headers are returned
as received, and part headers are not sent (see [Encoding Object](#encoding-object)), so the
declarations themselves are not used.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `description` | 2.0, 3.0+ | Not needed | Not used. |
| `required` | 3.0+ | Not needed | Not used. |
| `deprecated` | 3.0+ | Not needed | Not used. |
| `style` | 3.0+ | Not needed | Not used. |
| `explode` | 3.0+ | Not needed | Not used. |
| `schema` | 3.0+ | Not needed | Not used. |
| `content` | 3.0+ | Not needed | Not used. |
| `example` | 3.0+ | Not needed | Not used. |
| `examples` | 3.0+ | Not needed | Not used. |
| `type` | 2.0 | Converted | Moved into `schema`. |
| `format` | 2.0 | Converted | Moved into `schema`. |
| `items` | 2.0 | Converted | Moved into `schema`. |
| `collectionFormat` | 2.0 | Converted | Removed; OpenAPI 3 headers always use `style: simple`. |
| `default` | 2.0 | Converted | Moved into `schema`. |
| `maximum` | 2.0 | Converted | Moved into `schema`. |
| `exclusiveMaximum` | 2.0 | Converted | Moved into `schema`. |
| `minimum` | 2.0 | Converted | Moved into `schema`. |
| `exclusiveMinimum` | 2.0 | Converted | Moved into `schema`. |
| `maxLength` | 2.0 | Converted | Moved into `schema`. |
| `minLength` | 2.0 | Converted | Moved into `schema`. |
| `pattern` | 2.0 | Converted | Moved into `schema`. |
| `maxItems` | 2.0 | Converted | Moved into `schema`. |
| `minItems` | 2.0 | Converted | Moved into `schema`. |
| `uniqueItems` | 2.0 | Converted | Moved into `schema`. |
| `enum` | 2.0 | Converted | Moved into `schema`. |
| `multipleOf` | 2.0 | Converted | Moved into `schema`. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Tag Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `name` | 2.0, 3.0+ | Not needed | Documentation grouping. |
| `summary` | 3.2 | Not needed | Documentation grouping. |
| `description` | 2.0, 3.0+ | Not needed | Documentation grouping. |
| `externalDocs` | 2.0, 3.0+ | Not needed | Documentation only. |
| `parent` | 3.2 | Not needed | Documentation grouping. |
| `kind` | 3.2 | Not needed | Documentation grouping. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Reference Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `$ref` | 2.0, 3.0+ | Covered | In-document `#/` pointers and relative file references are inlined; a cycle is cut into a permissive object (see [recursive schemas](nw-connector-builder-codegen.md#recursive-schemas)). Absolute URLs are rejected, and so are relative file references when the spec itself was fetched by URL: fetching more documents would open the build to SSRF and supply-chain risks. |
| `summary` | 3.1+ | Not needed | Dropped during resolution; the target's text is used. Documentation only. |
| `description` | 3.1+ | Not needed | As `summary`. Parameter descriptions shown to the caller come from the target. |

## Schema Object

Generated actions have a flat contract: each top-level parameter or body property is one typed
argument. Type, enum and range constraints at that level are enforced by the input model before the
call. Below that level (nested objects, array items, composition keywords) the value is passed
through as given and the upstream API validates it. Output fields are not typed.

### OAS-specific Schema fields

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `nullable` | 3.0 | Partial | Accepted, but an argument set to `null` is treated as unset and never sent, so an explicit `null` cannot be sent. Optional arguments already accept `null`. |
| `discriminator` | 2.0, 3.0+ | Skipped | `oneOf` / `anyOf` bodies are a free-form object, so the discriminator has nothing to select. The 2.0 string form is converted to `{propertyName: ...}`, so the schema stays valid. Not planned. |
| `readOnly` | 2.0, 3.0+ | Partial | Top-level properties of a flattened body marked `readOnly` are left out. Nested `readOnly` properties are not removed. |
| `writeOnly` | 3.0+ | Not needed | Only matters for responses, whose fields are not typed. |
| `xml` | 2.0, 3.0+ | Not needed | XML bodies are passed as a string the caller writes, so XML naming rules are not applied. |
| `externalDocs` | 2.0, 3.0+ | Not needed | Documentation only. |
| `example` | 2.0, 3.0+ | Not needed | Schema-level examples are not used; generated tests use media type examples. |
| `deprecated` | 3.0+ | Not needed | The property is still offered and sent. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. 3.1+ schemas may carry other unknown keywords; they are ignored. |

### JSON Schema keywords

| Keyword | Versions | Status | How it is handled / why |
|---|---|---|---|
| `$id` | 3.1+ | Skipped | A `$ref` to a schema's `$id` URI is treated as a remote reference and rejects the spec. A `$id` nobody references is harmless. |
| `$schema` | 3.1+ | Skipped | Not read; the default OAS dialect is assumed. |
| `$ref` | 2.0, 3.0+ | Partial | JSON pointer and relative file references are inlined (see [Reference Object](#reference-object)). In 3.1+ a `$ref`'s sibling keywords should apply too, but they are dropped during resolution and only the target is used. |
| `$anchor` | 3.1+ | Skipped | A `$ref: "#name"` anchor reference cannot be resolved and fails the build. |
| `$dynamicRef` | 3.1+ | Skipped | Not resolved; the keyword is ignored and the schema is typed from its other keywords. |
| `$dynamicAnchor` | 3.1+ | Skipped | As `$dynamicRef`. |
| `$vocabulary` | 3.1+ | Not needed | Only meaningful in meta-schemas. |
| `$comment` | 3.1+ | Not needed | A comment. |
| `$defs` | 3.1+ | Covered | Reached through JSON pointer references such as `#/components/schemas/A/$defs/B`. |
| `allOf` | 2.0, 3.0+ | Partial | On an object request body, the parts are merged and flattened into arguments. Elsewhere the value is typed `Any` and passed through. |
| `anyOf` | 3.0+ | Skipped | Typed `Any` (a whole body becomes a free-form object); the upstream API validates. A flat contract cannot express alternatives. |
| `oneOf` | 3.0+ | Skipped | As `anyOf`. |
| `not` | 3.0+ | Skipped | Ignored; the upstream API validates. |
| `if` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `then` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `else` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `dependentSchemas` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `prefixItems` | 3.1+ | Skipped | Ignored; the array is typed from `items`, or `list[Any]`. |
| `items` | 2.0, 3.0+ | Partial | Types the array (`list[str]`, `list[int]`, ...). Item objects are `dict`, and constraints on items are not enforced. |
| `contains` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `properties` | 2.0, 3.0+ | Partial | Top-level properties of an object request body become one argument each. Nested objects are free-form `dict` arguments, so their properties are not shown or checked. |
| `patternProperties` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `additionalProperties` | 2.0, 3.0+ | Partial | An object with only `additionalProperties` is a free-form `dict`. A flattened body accepts only its declared properties, so extra properties cannot be sent. A cut recursive `$ref` becomes an object with `additionalProperties: true`. |
| `propertyNames` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `unevaluatedItems` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `unevaluatedProperties` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `type` | 2.0, 3.0+ | Covered | `string`, `integer`, `number`, `boolean`, `array` and `object` map to `str`, `int`, `float`, `bool`, `list[...]` and `dict`. A 3.1 type array with `"null"` becomes the single type plus nullable. A union of several types becomes `anyOf` and is typed `Any`. |
| `enum` | 2.0, 3.0+ | Partial | String, integer and boolean enums are enforced as a `Literal`; `null` members are dropped. Number (float) enums are advertised in the tool schema but not enforced, since a `Literal` cannot hold floats. Mixed-type enums are not enforced or advertised. |
| `const` | 3.1+ | Partial | A string, integer or boolean `const` is enforced as a one-value `Literal`. Object and array values are ignored. |
| `multipleOf` | 2.0, 3.0+ | Covered | Enforced on `integer` and `number` arguments. |
| `maximum` | 2.0, 3.0+ | Covered | Enforced on `integer` and `number` arguments. |
| `exclusiveMaximum` | 2.0, 3.0+ | Covered | Enforced in both forms: the 2.0/3.0 boolean beside `maximum` and the 3.1+ number. |
| `minimum` | 2.0, 3.0+ | Covered | Enforced on `integer` and `number` arguments. |
| `exclusiveMinimum` | 2.0, 3.0+ | Covered | Enforced in both forms, as `exclusiveMaximum`. |
| `maxLength` | 2.0, 3.0+ | Covered | Enforced on string arguments. |
| `minLength` | 2.0, 3.0+ | Covered | Enforced on string arguments. |
| `pattern` | 2.0, 3.0+ | Partial | Advertised in the tool schema, not enforced: spec patterns are ECMA-262 regular expressions, which Python does not always read the same way, and the upstream API validates. |
| `maxItems` | 2.0, 3.0+ | Covered | Enforced on array arguments. |
| `minItems` | 2.0, 3.0+ | Covered | Enforced on array arguments. |
| `uniqueItems` | 2.0, 3.0+ | Skipped | Not enforced or advertised; the upstream API validates. |
| `maxContains` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `minContains` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `maxProperties` | 2.0, 3.0+ | Skipped | Ignored; the upstream API validates. |
| `minProperties` | 2.0, 3.0+ | Skipped | Ignored; the upstream API validates. |
| `required` | 2.0, 3.0+ | Covered | Required properties of a required body become required arguments. |
| `dependentRequired` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `format` | 2.0, 3.0+ | Partial | Advertised in the tool schema, not enforced. `binary` and `byte` mark a base64 argument that is decoded to bytes before sending. |
| `contentEncoding` | 3.1+ | Partial | `base64` and `base64url` mark an argument decoded to bytes before sending; padding is optional for both. Other encodings are not decoded. |
| `contentMediaType` | 3.1+ | Covered | A media type that is not text, JSON or XML marks a base64 argument decoded to bytes before sending. |
| `contentSchema` | 3.1+ | Skipped | Ignored; the encoded content is passed through as a string. |
| `title` | 2.0, 3.0+ | Not needed | Titles are removed from the tool schema; argument names already carry them. |
| `description` | 2.0, 3.0+ | Covered | The argument's description: first sentence, capped at 90 characters. |
| `default` | 2.0, 3.0+ | Covered | The argument's default value. |
| `examples` | 3.1+ | Not needed | Schema-level examples are not used. |
| `definitions` | 3.1+ | Covered | As `$defs`: reached through JSON pointer references. |
| `dependencies` | 3.1+ | Skipped | Ignored; the upstream API validates. |
| `$recursiveRef` | 3.1+ | Skipped | Not resolved; ignored as `$dynamicRef`. |
| `$recursiveAnchor` | 3.1+ | Skipped | As `$recursiveRef`. |
| Boolean schema (`true` / `false`) | 3.1+ | Covered | Accepted as a property schema or a parameter's `schema`, typed `Any` (free-form). |

### Data type formats

| Format | Versions | Status | How it is handled / why |
|---|---|---|---|
| `int32` | 2.0, 3.0+ | Covered | `int`; the format is advertised. Range is not checked. |
| `int64` | 2.0, 3.0+ | Covered | `int`; the format is advertised. |
| `float` | 2.0, 3.0+ | Covered | `float`; the format is advertised. |
| `double` | 2.0, 3.0+ | Covered | `float`; the format is advertised. |
| `byte` | 2.0, 3.0 | Covered | The caller passes base64, which is decoded before sending. |
| `binary` | 2.0, 3.0 | Covered | The caller passes base64, which is decoded before sending. |
| `date` | 2.0, 3.0 | Partial | `str`; the format is advertised, not validated. |
| `date-time` | 2.0, 3.0 | Partial | `str`; the format is advertised, not validated. |
| `password` | 2.0, 3.0+ | Not needed | A UI hint. Advertised as a format. |
| `type: file` | 2.0 | Converted | Becomes `type: string, format: binary` wherever it appears. On `formData` parameters it also makes the body multipart. |

## Discriminator Object

Bodies and values built with `oneOf` / `anyOf` are free-form (see
[JSON Schema keywords](#json-schema-keywords)), so there is nothing for a discriminator to select.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `propertyName` | 3.0+ | Skipped | Ignored; the caller sets the property in the free-form value, and the upstream API validates. |
| `mapping` | 3.0+ | Skipped | As `propertyName`. |
| `defaultMapping` | 3.2 | Skipped | As `propertyName`. |
| `^x-` | 3.1+ | Not needed | Removed before resolution. |

## XML Object

XML bodies are a string the caller writes, so XML mapping rules are not applied.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `nodeType` | 3.2 | Not needed | XML presentation of a JSON-modeled schema. |
| `name` | 2.0, 3.0+ | Not needed | As `nodeType`. |
| `namespace` | 2.0, 3.0+ | Not needed | As `nodeType`. |
| `prefix` | 2.0, 3.0+ | Not needed | As `nodeType`. |
| `attribute` | 2.0, 3.0+ | Not needed | As `nodeType`. |
| `wrapped` | 2.0, 3.0+ | Not needed | As `nodeType`. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

## Security Scheme Object

The connector has one default scheme: a global self-managed scheme (API key, bearer, basic) if
there is one, else the one most operations use; host-supplied OAuth2 / OpenID Connect schemes are
picked only when there is no self-managed one. Operations that need another presentable scheme send
it through a named entry under `auth_schemes:`
(see [per-action auth schemes](nw-connector-builder-scope.md#per-action-auth-schemes)).

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `type` | 2.0, 3.0+ | Partial | `apiKey`, `http`, `oauth2` and `openIdConnect` are supported (see the variants below). `mutualTLS` is not. |
| `description` | 2.0, 3.0+ | Not needed | Documentation only. |
| `name` | 2.0, 3.0+ | Covered | The header or query parameter name for an `apiKey`. A parameter that repeats the credential (`api_key`, `token`, and similar names) is removed from the action's arguments, and the report notes it. |
| `in` | 2.0, 3.0+ | Partial | `header` and `query` are supported; `cookie` is not. |
| `scheme` | 3.0+ | Partial | `bearer` and `basic` are supported. Other HTTP schemes (`digest`, `negotiate`, ...) are not. |
| `bearerFormat` | 3.0+ | Not needed | A hint about the token; the token is sent as given. |
| `flows` | 3.0+ | Covered | Any flow makes the scheme a host-supplied bearer token (see [OAuth Flows Object](#oauth-flows-object)). |
| `openIdConnectUrl` | 3.0+ | Not needed | The scheme is a host-supplied bearer token; Node Wire never runs discovery. |
| `oauth2MetadataUrl` | 3.2 | Not needed | Documentation for the host, which obtains the token. |
| `deprecated` | 3.2 | Not needed | The scheme is still used. |
| `flow` | 2.0 | Converted | `implicit`, `password`, `application` and `accessCode` become the 3.0 `implicit`, `password`, `clientCredentials` and `authorizationCode` flows. |
| `authorizationUrl` | 2.0 | Converted | Moved onto the converted flow. |
| `tokenUrl` | 2.0 | Converted | Moved onto the converted flow. |
| `scopes` | 2.0 | Converted | Moved onto the converted flow. |
| `^x-` | 2.0, 3.0+ | Not needed | Removed before resolution. |

### Security scheme variants

| Variant | Versions | Status | How it is handled / why |
|---|---|---|---|
| `basic` | 2.0 | Converted | Becomes `type: http, scheme: basic`. |
| `apiKey`, `in: header` | 2.0, 3.0+ | Covered | `static_token` provider sending `<ID>_API_KEY` in the named header. |
| `apiKey`, `in: query` | 2.0, 3.0+ | Covered | `apikey_query` provider adding `<ID>_API_KEY` to the query string. |
| `apiKey`, `in: cookie` | 3.0+ | Skipped | No cookie auth provider yet. Operations that only accept it are soft-dropped. Candidate for later. |
| `http`, `scheme: basic` | 3.0+ | Covered | `static_token` provider sending `Authorization: Basic` with `<ID>_BASIC_AUTH`, base64-encoded. |
| `http`, `scheme: bearer` | 3.0+ | Covered | `static_token` provider sending `Authorization: Bearer <ID>_TOKEN`. |
| `http`, other schemes | 3.0+ | Skipped | `digest` and similar schemes are challenge-response exchanges, not a fixed header. Operations that only accept them are soft-dropped. Not planned. |
| `oauth2` (single `flow`) | 2.0 | Converted | Becomes a `flows` object (see `flow` above). |
| `oauth2` (`flows`) | 3.0+ | Covered | Host-supplied bearer: `<ID>_ACCESS_TOKEN` is sent as `Authorization: Bearer`. Node Wire never obtains or refreshes it. |
| `openIdConnect` | 3.0+ | Covered | Host-supplied bearer, as `oauth2`. |
| `mutualTLS` | 3.1+ | Skipped | A client certificate is configured on the TLS connection, not sent as a header or parameter, and the runtime has no per-connector TLS client configuration. Operations that only accept it are soft-dropped. Candidate for later. |

## OAuth Flows Object

Every flow maps to the same host-supplied bearer token, so which flows are declared does not change
the generated connector.

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `implicit` | 3.0+ | Covered | Host-supplied bearer. |
| `password` | 3.0+ | Covered | Host-supplied bearer. |
| `clientCredentials` | 3.0+ | Covered | Host-supplied bearer. |
| `authorizationCode` | 3.0+ | Covered | Host-supplied bearer. |
| `deviceAuthorization` | 3.2 | Covered | Host-supplied bearer. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## OAuth Flow Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `authorizationUrl` | 3.0+ | Not needed | Copied into the build report's auth notes for the host. Node Wire never calls it. |
| `deviceAuthorizationUrl` | 3.2 | Not needed | Node Wire never calls it; the host obtains the token. Not copied into the report. |
| `tokenUrl` | 3.0+ | Not needed | Copied into the build report's auth notes for the host. Node Wire never calls it. |
| `refreshUrl` | 3.0+ | Not needed | Node Wire never refreshes the token; the host does. |
| `scopes` | 3.0+ | Skipped | Node Wire does not request tokens, so it does not request scopes. The host must obtain a token with the right scopes. By design, not planned. |
| `^x-` | 3.0+ | Not needed | Removed before resolution. |

## Scopes Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{name}` | 2.0 | Converted | Moved to the converted flow's `scopes`. |
| `^x-` | 2.0 | Not needed | Removed before resolution. |

## Security Requirement Object

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{name}` | 2.0, 3.0+ | Partial | Alternatives (OR): the operation uses the connector default if it is listed, else the first presentable alternative through `auth_schemes:`. `{}` (optional auth) still sends the default credential, and so does an operation with no `security` anywhere when the connector has a scheme. `security: []` makes the action anonymous. A requirement naming several schemes (AND) sends all of them when each puts its credential in a different place; two that both need `Authorization` soft-drop the operation with that reason. An operation whose alternatives are all unsupported is soft-dropped. Scope and role lists are not used. Not covered: 3.2 keys given as a URI of a Security Scheme are not resolved, so that alternative is treated as unsupported. |

## Specification extensions

| Field | Versions | Status | How it is handled / why |
|---|---|---|---|
| `^x-` on any object | 2.0, 3.0+ | Not needed | Removed before `$ref` resolution; the generator reads none of them. Property and header names that start with `x-` are names, not extensions, and are kept. Example data is left as is. |
| Unprefixed unknown keywords in a Schema | 3.1+ | Not needed | Ignored. |

## Cross-cutting rules

### HTTP methods

| Method | Versions | Status | How it is handled / why |
|---|---|---|---|
| `get` | 2.0, 3.0+ | Covered | A declared request body is sent in 3.1+ documents and ignored in 3.0. |
| `put` | 2.0, 3.0+ | Covered | |
| `post` | 2.0, 3.0+ | Covered | |
| `delete` | 2.0, 3.0+ | Covered | As `get`. |
| `options` | 2.0, 3.0+ | Covered | |
| `head` | 2.0, 3.0+ | Covered | As `get`. |
| `patch` | 2.0, 3.0+ | Covered | |
| `trace` | 3.0+ | Covered | |
| `query` | 3.2 | Covered | Sent as `QUERY`. |
| Any other method (`additionalOperations`) | 3.2 | Covered | Sent with the method name in upper case. |

### `$ref` sibling rules

| Context | Versions | Status | How it is handled / why |
|---|---|---|---|
| Reference Object siblings | 2.0, 3.0+ | Not needed | Dropped; the target is used. In 3.1+ only `summary` and `description` may override, and both are documentation. |
| Schema `$ref` siblings | 3.1+ | Partial | Dropped; only the target schema is used, so a sibling such as `maxLength` beside a `$ref` is not applied. |
| Path Item `$ref` siblings | 2.0, 3.0+ | Partial | Dropped; the referenced Path Item replaces the local fields, including any operations defined next to the `$ref`. |

### Server URL templating

| Rule | Versions | Status | How it is handled / why |
|---|---|---|---|
| Template variables in `url` | 3.0+ | Covered | Replaced with each variable's `default`. |
| Each variable has a `default` | 3.0+ | Covered | Required: a root server variable without one fails the build. |
| `enum` values | 3.0+ | Skipped | Not offered as a choice; the default is used. |
| Relative server URLs | 3.0+ | Partial | Resolved against the fetch URL for a spec fetched over HTTP(S), root `servers` only. For a local file, pass `--base-url`. A relative operation-level server is ignored with a note. |
| No query or fragment in `url` | 3.1+ | Not needed | A spec rule; a URL that breaks it is used as written. |

### Path templating

| Rule | Versions | Status | How it is handled / why |
|---|---|---|---|
| `{name}` marks a replaceable segment | 2.0, 3.0+ | Covered | Filled from the path parameter, percent-encoded. |
| Every template expression has a path parameter | 3.0+ | Covered | Checked by validation; an operation without one is soft-dropped as invalid. |
| Path values contain no unescaped `/`, `?` or `#` | 3.1+ | Covered | Path values are fully percent-encoded, so `/` cannot add a segment. With `allowReserved`, reserved characters other than `&`, `=` and `#` are kept, as the spec allows. |

### Response codes

| Rule | Versions | Status | How it is handled / why |
|---|---|---|---|
| Explicit status codes | 2.0, 3.0+ | Covered | The lowest `2xx` is the success response. |
| `default` | 2.0, 3.0+ | Covered | The success response only when no `2xx` is declared. |
| Ranges `1XX`–`5XX` | 3.0+ | Partial | `2XX` is read after explicit `2xx` codes. Other ranges are not read; error replies raise by status. |
| Explicit code overrides its range | 3.0+ | Covered | Explicit `2xx` codes are tried before `2XX`. |

## Not covered, and why

Every **Skipped** row above, grouped. "Candidate for later" means the gap could be closed without
changing the generator's flat-contract or host-supplied-auth design.

**Inbound traffic** (the API calls you; a connector only makes outbound calls)

- `webhooks` and `callbacks`: requests the API sends, not calls a connector makes. Noted in the
  build report. Not planned.

**Server choice**

- Server variable `enum`: the default is used. Pick another value with `base_url` in
  `connectors.yaml` or `--base-url`. Not planned.

**Parameters and bodies**

- Swagger 2.0 `collectionFormat: tsv`: OpenAPI 3 has no equivalent; an optional parameter is
  left out, a required one soft-drops the operation. Not planned.
- Encoding Object `style`, `explode`, `allowReserved`: form fields always use form/explode, objects
  as JSON text. Candidate for later.
- Encoding Object `headers`: per-part multipart headers are not sent. Candidate for later.
- 3.2 sequential and streaming media (`itemSchema`, `prefixEncoding`, `itemEncoding`, nested
  Encoding `encoding` / `prefixEncoding` / `itemEncoding`): responses are read whole, and requests
  are not serialized as sequences. Not planned.

**Schema structure below the top level** (flat tool contract; the upstream API validates)

- `anyOf`, `oneOf`, `not`, `discriminator` (`propertyName`, `mapping`, 3.2 `defaultMapping`):
  typed `Any`, or a free-form `body` object. Not planned.
- `if` / `then` / `else`, `dependentSchemas`, `dependentRequired`, `dependencies`, `prefixItems`,
  `contains`, `minContains`, `maxContains`, `patternProperties`, `propertyNames`,
  `unevaluatedItems`, `unevaluatedProperties`, `uniqueItems`, `minProperties`, `maxProperties`,
  `contentSchema`: ignored. Not planned.
- `$id`, `$anchor`, `$dynamicRef`, `$dynamicAnchor`, `$recursiveRef`, `$recursiveAnchor`:
  references by URI or anchor are not resolved. A `$ref` to an `$id` URI is rejected as remote, and
  a `$ref` to an anchor fails the build. Candidate for later.
- `$schema` and `jsonSchemaDialect`: the default OAS dialect is assumed. Not planned.
- 3.2 `$self`: relative references resolve against the file or fetch URL instead. Not planned.

**Auth** (Node Wire presents credentials; it does not run auth protocols)

- `mutualTLS`: a client certificate is TLS configuration, not a header or parameter, and the
  runtime has no per-connector TLS client settings. Candidate for later.
- `apiKey` in `cookie`: no cookie auth provider. Candidate for later.
- `http` schemes other than `bearer` and `basic` (`digest`, ...): challenge-response, not a fixed
  header. Not planned.
- OAuth2 token acquisition and `scopes`: the host supplies the token by design. Not planned.
- 3.2 security requirement keys given as a URI of a Security Scheme: not resolved, so the
  alternative is treated as unsupported. Candidate for later.

**Examples** (no effect on calls; only generated tests use examples)

- Example `serializedValue`: would need a parser per media type to turn back into input data.
  Not planned.
- Example `externalValue`: not fetched, the same policy as remote `$ref`s. Not planned.

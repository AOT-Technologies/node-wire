# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# nw-connector-builder

Generate a `node_wire_<id>` connector (and optionally an MCP server) from a
Swagger 2.0 / OpenAPI 3.x document.

```bash
# from repo root, with the package installed
nw-connector-builder path/to/openapi.yaml --id my_api --no-mcp
nw-connector-builder https://example.com/openapi.json --id my_api --wire --force
```

See the hand-off spec under `.scratch/nw-connector-builder/spec.md` for the
full contract (auth mapping, soft-drop rules, staging gate, MCP hand-off).

Environment:

- `NW_REST_ALLOWED_HOSTS` — egress allowlist for generated `RestConnector`s
- `NW_REST_TRUST_ENV` — set `true` to honor `HTTP(S)_PROXY` (default off)

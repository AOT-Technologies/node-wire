<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-http

Generic HTTP connector for Node Wire: issue `GET`/`POST`/`PUT`/`PATCH`/`DELETE` requests to an arbitrary URL through the Node Wire runtime, with its resilience, observability, and scope policies applied.

Outbound requests are checked against an SSRF policy — loopback, private, and link-local targets and cloud metadata endpoints are refused, both at schema validation and again against the resolved IP at connection time. Set `NW_HTTP_GENERIC_ALLOWED_HOSTS` (comma- or space-separated hostnames) to restrict egress to an explicit allowlist instead.

Requires `node-wire-runtime`. Registers itself under the `http_generic` connector key via the `node_wire.connectors` entry point.

Install from PyPI as `node-wire-http`, or from this monorepo via `packages/connectors/http_generic`.

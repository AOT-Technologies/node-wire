<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Versioning, Stability & Deprecation Policy

Node Wire follows [Semantic Versioning 2.0.0](https://semver.org/). As of
**1.0.0**, the public API is stable and covered by the guarantees below.

## Semantic Versioning

Given a version `MAJOR.MINOR.PATCH`:

- **MAJOR** — backward-incompatible changes to the public API.
- **MINOR** — new, backward-compatible functionality (including new connectors).
- **PATCH** — backward-compatible bug fixes.

Within a major version (`1.x`) we do **not** make breaking changes to the public
API. Breaking changes are reserved for the next major (`2.0.0`).

## What is "public" (covered by SemVer)

The stability guarantee covers the surface enumerated in the
[Public API Reference](public-api.md):

- Symbols exported from `node_wire_runtime` (its `__all__`).
- The connector authoring contract: `BaseConnector`, `@nw_action` / `@sdk_action`,
  `ConnectorResponse`, error categories, and the auth/secret provider base classes.
- The connector entry-point group `node_wire.connectors`.
- The REST, gRPC, and MCP wire contracts (request/response shapes and routes).
- Documented configuration: the `connectors.yaml` schema and `NW_*` environment variables.
- Console entry points (`node-wire`, `nw-*`).

## What is NOT public (may change in any release)

- Any module, class, function, or attribute whose name begins with `_`.
- Anything in internal modules not re-exported from a package's `__init__`.
- Generated code (`*_pb2.py`, `*_pb2_grpc.py`) beyond the documented gRPC service contract.
- Test suites, the `playground/` package, build/packaging scripts, and CI config.
- Exact log message text, internal telemetry attribute names, and temp-file layouts.

If you depend on something not listed as public, pin an exact version and expect
it may change without a major bump.

## Deprecation policy

Public API is removed only across a major version. Before removal:

1. The API is marked **deprecated** in at least one **minor** release beforehand.
2. Deprecated behavior keeps working for the remainder of the current major series.
3. Where practical, use raises a `DeprecationWarning` and/or is flagged in the
   connector manifest (actions support a `deprecated` flag) and in the changelog.
4. Removal happens only in the next `MAJOR` release, with migration notes in the changelog.

Connector *actions* and MCP tool arguments can be deprecated individually via the
manifest `deprecated` flag; deprecated MCP arguments follow a phased removal.

## Supported versions

Security and bug fixes target the latest `1.x` release and `main`. See
[SECURITY.md](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md) for the support matrix and reporting process.

## Release process

Stable releases are tagged `vMAJOR.MINOR.PATCH`, published to PyPI via Trusted
Publisher (OIDC) with Sigstore attestations, and recorded in
[CHANGELOG.md](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md).
See [packaging.md](packaging.md#release-process-tag-first) for the tag-first
operator flow.

### PyPI pre-releases (beta)

Optional pre-releases use [PEP 440](https://packaging.python.org/en/latest/specifications/version-specifiers/)
forms such as `1.2.0b1`, `1.2.0a1`, or `1.2.0rc1` (tags `v1.2.0b1`, …). These are
**not** SemVer pre-release tags (`1.2.0-beta.1`). They publish to the same PyPI
projects as stable builds and do **not** create a GitHub Release. Public API
guarantees in this document apply to the final `MAJOR.MINOR.PATCH` release; a
pre-release does not change those guarantees early. Install with
`pip install --pre …` or an exact version pin.

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-27

First stable release. The public API is now **frozen under Semantic Versioning** —
see [docs/versioning.md](docs/versioning.md) for the stability and deprecation
policy and [docs/public-api.md](docs/public-api.md) for the supported surface.

### Added

- Versioning, stability, and deprecation policy (`docs/versioning.md`).
- Public API reference enumerating the frozen surface (`docs/public-api.md`).
- `node_wire_runtime.__version__`.
- DCO sign-off enforcement, Dependabot, and `SUPPORT` / `ROADMAP` / `GOVERNANCE` docs.
- Test-coverage gate (`fail_under`).

### Changed

- Promoted from Beta to **Production/Stable**; all nine packages versioned `1.0.0`.
- Connectors now require `node-wire-runtime>=1.0.0`.

## [0.1.0] - 2026-06-26

### Added

- Initial public release of the Node Wire platform: runtime, connectors, and bindings.
- Nine publishable Python packages: runtime plus eight connectors (HTTP generic, Google Drive, SMTP, Stripe, Epic FHIR, Cerner FHIR, Salesforce, Slack).
- REST, gRPC, and MCP entrypoints with authentication, scope policy, and observability hooks.
- Per-connector MCP Docker images and unified MCP server (`agents.mcp_entrypoint`).
- ToolHive agent scenario documentation and sample agent workflow.
- CI quality gates: Ruff, Mypy, pytest, Bandit, pip-audit, and REUSE compliance.
- Governance docs: contributing guide, security policy, code of conduct, privacy notes, and HIPAA considerations.

### Fixed

- gRPC protobuf stubs committed and importable for production startup.
- REST API no longer requires the optional `playground` package at import time.
- Dependency lockfile upgraded to resolve known CVEs in transitive packages.
- Packaging, publish workflow, and security scanning aligned on the nine-package surface.

[1.0.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.0.0
[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0

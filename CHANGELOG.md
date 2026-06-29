# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0

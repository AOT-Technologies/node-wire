<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Reading map

Which page to read, in what order. For the audience entry points, start at the [home page](index.md).

## Foundation

Read these in order before any topic page. Each one assumes the pages above it.

| # | Page | Answers |
|---|---|---|
| 1 | [Architecture overview](architecture.md) | What the three layers (runtime, connectors, bindings) are, and how a request moves through them |
| 2 | [Domain](architecture/domain.md) | What connector, action, manifest, `ConnectorResponse`, `ErrorCategory`, tenant pin and named config mean |
| 3 | [Seams](architecture/seams.md) | Where the layers hand off: `ConnectorFactory`, `invoke.py`, `PolicyHook`, `SecretProvider`, `AuthProvider`, `ErrorMapper` |
| 4 | [The `run()` pipeline](architecture.md#the-run-pipeline) | What happens to every action: pin check → validation → policy → resilience → execute → error mapping |

## Topic branches

Pick the branch you need. "Read first" names the pages it assumes, in addition to the foundation.

| Topic | Page | Answers | Read first |
|---|---|---|---|
| Tenancy | [Tenancy](architecture/tenancy.md) | Tenant resolution, entitlement by config, tenant secrets, MCP tenant tools | — |
| Bindings | [Connectors on REST, MCP and gRPC](connector-bindings.md) | How actions become routes, tools and methods; the manifest; status mapping | — |
| Use | [Use a connector](connectors.md) | Calling a shipped connector in-process or over REST, per tenant | Tenancy |
| Use | [Connector reference](connector-reference.md) | Factory API, `connectors.yaml` keys, the catalog of shipped connectors | Use a connector |
| Auth | [Build a connector — Authentication](connectors-build.md#authentication) | Auth provider types and their `connectors.yaml` fields | Seams |
| Build | [Build a connector](connectors-build.md) | Writing a connector by hand | Seams |
| Codegen | [CLI overview](cli/index.md) → [nw CLI](cli/nw-cli.md) | Generating a connector, wheels and an MCP host from an OpenAPI spec | Build a connector (skim) |
| Codegen | [Generator contract](cli/nw-connector-builder-scope.md) | What the OpenAPI generator supports and deliberately does not | nw-connector-builder |
| MCP | [MCP overview](mcp.md) | Which of the three MCP deployment paths to use; transports; tool search | Bindings |
| MCP | [stacklok MCP servers](stacklok-mcp-servers.md) | `nw gen-stacklok` servers behind ToolHive | MCP overview, Tenancy |
| MCP | [ToolHive agent scenario](toolhive_agent_scenario.md) | Running an agent end to end against ToolHive | MCP overview |
| Ship | [Packaging](packaging.md) | Wheels, the new-connector ship checklist, PyPI release | Build a connector |
| Ship | [Local wheels → images](local-packages-to-images.md) | Building and composing the per-connector MCP images | Packaging |

## Operator tasks

Task pages you can open directly: [Installation](installation.md) · [Configuration](configuration.md) ·
[Troubleshooting](troubleshooting.md) · [Release & rollback](release-rollback.md) ·
[Versioning](versioning.md) · [Privacy](privacy.md) · [HIPAA considerations](compliance/hipaa-considerations.md).

## Why things are the way they are

Decisions are frozen records, separate from the pages above: [Architecture decision records](adr/index.md).

## Who owns what

One fact, one owning page. Other pages link here instead of restating it.

| Fact | Owner |
|---|---|
| Quick start | [Home](index.md#quick-start) |
| Prerequisites, dependency install, optional telemetry stack | [Installation](installation.md) |
| Every `NW_*` variable and its default | [Configuration](configuration.md) |
| The three layers | [Architecture](architecture.md) |
| Tenant resolution, entitlement, tenant secrets, MCP tenant tools | [Tenancy](architecture/tenancy.md) |
| Auth provider types and fields | [Build a connector — Authentication](connectors-build.md#authentication) |
| Scope policy for callers | [Build a connector — Security](connectors-build.md#security-rest-plugins-secrets) |
| Per-connector actions, auth and secrets | [Connector reference — catalog](connector-reference.md#connector-catalog) |
| Ship checklist for a new connector (runtime, PyPI, MCP image) | [Packaging](packaging.md#adding-a-new-publishable-connector) |
| Building and composing MCP images | [Local wheels → images](local-packages-to-images.md) |
| MCP deployment paths | [MCP overview](mcp.md#which-mcp-path) |
| Which CLI command to run | [CLI overview](cli/index.md) |
| What the OpenAPI generator supports | [Generator contract](cli/nw-connector-builder-scope.md) |
| Local quality commands | [Code quality](code-quality-compliance.md) |
| CI gates and branch protection | [Quality & security gates](quality-security-gates.md) |
| Error codes, categories and what each surface returns | [Errors](errors.md), checked by `tests/test_docs_lifecycle.py` |
| Stable public API | [Public API](public-api.md), checked by `tests/test_docs_lifecycle.py` |

## Changing the docs

The rules are recorded in [ADR 0002](adr/0002-documentation-lifecycle.md). When a change touches documented behaviour:

1. Find the owning page in the table above. If the fact has no owner yet, pick one and add a row.
2. Check the claim against the code, not against another page.
3. Edit the owning page in place. If you find a copy elsewhere, delete it and link to the owner.
4. Keep bug chronology, repair notes and "fixed along the way" lists in the PR and `CHANGELOG.md`.
   Living pages say what is true now. They carry no `Status:` line.
5. A new decision is a new ADR. Never rewrite an accepted one.
6. When you move a page, leave a short redirect stub at the old path, outside the navigation.

`mkdocs build --strict` fails on broken links. `uv run pytest tests/test_docs_lifecycle.py` fails
when a living page carries a `Status:` line, when an ADR is in the wrong part of the navigation, or
when the public API or package inventory no longer matches the code.

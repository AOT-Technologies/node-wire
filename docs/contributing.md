<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Contributing to Node Wire

Thanks for your interest in contributing! This guide covers how to set up a
development environment, the quality checks we enforce, and the conventions for
submitting changes. By contributing you agree that your contributions are
licensed under the project's [Apache License 2.0](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE) and that you sign off
each commit under the
[Developer Certificate of Origin](#developer-certificate-of-origin-dco).

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | Required to run the platform |
| `uv` | Latest | Recommended for dependency management and reproducible installs |
| Git | Any recent version | |
| Docker | Latest | Only needed for MCP server image builds |

## Development Setup

Install the project and all development dependencies from the committed
lockfile:

```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
uv sync --frozen --all-extras --dev
```

Install the pre-commit hooks so checks run automatically before each commit:

```bash
pre-commit install
```

## Quality Checks

All of the following run in CI on pull requests against `main`. Run them
locally before opening a PR:

- **Lint:** `uv run ruff check .`
- **Auto-fix & format:** `uv run ruff check --fix . && uv run ruff format .`
- **Type-check:** `uv run mypy`
- **Security (SAST):** `uv run bandit -c pyproject.toml -r src`
- **Tests:** `uv run pytest`

See [Code Quality](code-quality-compliance.md) and [Quality & Security Gates](quality-security-gates.md) for the full tooling reference.

## Licensing & REUSE Compliance

This project is [REUSE](https://reuse.software/) compliant. **Every new file
must carry an SPDX header.** For source and Markdown files use the project's
standard header, e.g. for Python:

```python
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0
```

and for Markdown/HTML, an HTML comment with the same two SPDX tags.

## Git Identity

Configure your git identity before committing. Commits must use your real
name and a valid email so attribution is accurate:

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

## Developer Certificate of Origin (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) instead of a CLA. Add a `Signed-off-by` trailer to **every commit**:

```bash
git commit -s -m "Your commit message"
```

CI (`.github/workflows/dco.yml`) verifies that every commit in a pull request
carries a sign-off matching its author. If you forget:

```bash
git rebase --signoff main
git push --force-with-lease
```

## Submitting Changes

1. Fork the repository and create a feature branch from `main`.
2. Make your change, including tests and documentation updates where relevant.
   Sign off every commit with `git commit -s`.
3. Ensure all quality checks above pass locally.
4. Open a pull request against `main` with a clear description and motivation.
5. A maintainer will review your PR. Address feedback by pushing additional
   commits to your branch.

## Reporting Bugs & Requesting Features

Use the GitHub issue templates. For **security vulnerabilities**, do not open a
public issue — follow the [Security Policy](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md) instead.

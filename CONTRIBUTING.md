<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Contributing to Node Wire

Thanks for your interest in contributing! This guide covers how to set up a
development environment, the quality checks we enforce, and the conventions for
submitting changes. By contributing you agree that your contributions are
licensed under the project's [Apache License 2.0](LICENSE) and that you sign off
each commit under the
[Developer Certificate of Origin](#developer-certificate-of-origin-dco).

Please also read our [Code of Conduct](CODE_OF_CONDUCT.md).

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
- **Type-check:** `uv run mypy` (uses the `src` target from `pyproject.toml`;
  avoid `mypy .`, which pulls in packaging `setup.py` files and produces
  duplicate-module noise)
- **Security (SAST):** `uv run bandit -c pyproject.toml -r src`
- **Tests:** `uv run pytest`

See [docs/code-quality-compliance.md](docs/code-quality-compliance.md) and
[docs/quality-security-gates.md](docs/quality-security-gates.md) for the full
tooling reference.

## Licensing & REUSE Compliance

This project is [REUSE](https://reuse.software/) compliant. **Every new file
must carry an SPDX header.** For source and Markdown files use the project's
standard header, e.g. for Python:

```python
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0
```

and for Markdown/HTML, an HTML comment with the same two SPDX tags. The
pre-commit and CI checks will fail if a file is missing its header.

## Git Identity

> **Configure your git identity before committing.** Commits must use your real
> name and a valid email so attribution is accurate:
>
> ```bash
> git config user.name "Your Name"
> git config user.email "you@example.com"
> ```
>
> Do not commit with placeholder identities (e.g. an unconfigured `My Name`
> default). Misconfigured identities get carried into the project history via
> squash-merge co-author trailers and are difficult to remove later.

## Developer Certificate of Origin (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) instead of a CLA. There is no agreement to sign and no paperwork — you
simply add a `Signed-off-by` trailer to **every commit**, certifying that you
have the right to submit it under the project's license.

Git adds the trailer automatically when you commit with `-s`:

```bash
git commit -s -m "Your commit message"
```

This appends a line matching your configured git identity (so set it correctly
first, per the section above):

```
Signed-off-by: Your Name <you@example.com>
```

CI (`.github/workflows/dco.yml`) verifies that every commit in a pull request
carries a sign-off matching its author. If you forget, add sign-offs to your
branch's commits and force-push:

```bash
git rebase --signoff main
git push --force-with-lease
```

By signing off, you certify the following:

> Developer Certificate of Origin
> Version 1.1
>
> Copyright (C) 2004, 2006 The Linux Foundation and its contributors.
>
> Everyone is permitted to copy and distribute verbatim copies of this license
> document, but changing it is not allowed.
>
> Developer's Certificate of Origin 1.1
>
> By making a contribution to this project, I certify that:
>
> (a) The contribution was created in whole or in part by me and I have the
>     right to submit it under the open source license indicated in the file; or
>
> (b) The contribution is based upon previous work that, to the best of my
>     knowledge, is covered under an appropriate open source license and I have
>     the right under that license to submit that work with modifications,
>     whether created in whole or in part by me, under the same open source
>     license (unless I am permitted to submit under a different license), as
>     indicated in the file; or
>
> (c) The contribution was provided directly to me by some other person who
>     certified (a), (b) or (c) and I have not modified it.
>
> (d) I understand and agree that this project and the contribution are public
>     and that a record of the contribution (including all personal information
>     I submit with it, including my sign-off) is maintained indefinitely and
>     may be redistributed consistent with this project or the open source
>     license(s) involved.

## Submitting Changes

1. Fork the repository and create a feature branch from `main`
   (e.g. `feature/short-description` or `fix/short-description`).
2. Make your change, including tests and documentation updates where relevant.
   Sign off every commit with `git commit -s` (see
   [DCO](#developer-certificate-of-origin-dco)).
3. Ensure all quality checks above pass locally.
4. Open a pull request against `main` with a clear description of the change and
   the motivation behind it. Link any related issue.
5. A maintainer will review your PR. Address review feedback by pushing
   additional commits to your branch.

## Reporting Bugs & Requesting Features

Use the GitHub issue templates. For **security vulnerabilities**, do not open a
public issue — follow the [Security Policy](SECURITY.md) instead.

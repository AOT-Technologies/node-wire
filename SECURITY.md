<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Security Policy

The Node Wire maintainers take the security of the project seriously. This
document explains which versions receive security fixes and how to report a
vulnerability responsibly.

## Supported Versions

Node Wire follows [Semantic Versioning](https://semver.org/). Security fixes are
applied to the latest 1.x release and the `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report suspected vulnerabilities privately through either of the following:

- **GitHub Security Advisories** — use the
  [private vulnerability reporting](https://github.com/AOT-Technologies/node-wire/security/advisories/new)
  form for this repository (preferred).
- **Email** — send details to **opensource@aot-technologies.com**.

When reporting, please include as much of the following as you can:

- A description of the vulnerability and its potential impact.
- The component or file involved (e.g. a connector, binding, or runtime module).
- Steps to reproduce, including a minimal proof of concept if available.
- The version, commit, or deployment configuration affected.

## What to Expect

- **Acknowledgement** within 3 business days of your report.
- An initial assessment and severity triage within 10 business days.
- Coordinated disclosure: we will work with you on a fix and a public
  disclosure timeline, and credit you in the advisory unless you prefer to
  remain anonymous.

Please give us a reasonable opportunity to remediate the issue before any
public disclosure.

## Scope

This policy covers the code in this repository: the runtime, connectors, and
bindings. Vulnerabilities in third-party dependencies should be reported to the
relevant upstream project; if a dependency issue affects Node Wire users, we
still welcome a heads-up so we can pin or patch accordingly.

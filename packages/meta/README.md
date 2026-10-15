<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-connectors

One install for the [Node Wire](https://github.com/AOT-Technologies/node-wire) runtime and every
official connector.

```bash
pip install node-wire-connectors
```

## What is a meta package?

`node-wire-connectors` contains no code. It is a metadata-only package whose only job is to
depend on other packages, so installing it installs the Node Wire runtime and all eight
connectors at compatible versions. Each connector is still its own package on PyPI, imported and
upgraded on its own; this package just saves you listing them.

Use it when you want every connector available, for example in a Node Wire host or an MCP server
that serves several systems. If you need only one or two, install those packages directly instead
(see [Install a subset](#install-a-subset)).

## What it includes

| Package | Connector ID | Connects to |
|---|---|---|
| [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/) | n/a | The connector framework all of the packages below run on |
| [`node-wire-http`](https://pypi.org/project/node-wire-http/) | `http_generic` | Any REST endpoint (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`), with SSRF protection |
| [`node-wire-smtp`](https://pypi.org/project/node-wire-smtp/) | `smtp` | Email, through a server-pinned SMTP relay |
| [`node-wire-stripe`](https://pypi.org/project/node-wire-stripe/) | `stripe` | Stripe payment intents, charges, subscriptions and refunds |
| [`node-wire-slack`](https://pypi.org/project/node-wire-slack/) | `slack` | Slack channel messages, direct messages and file uploads |
| [`node-wire-salesforce`](https://pypi.org/project/node-wire-salesforce/) | `salesforce` | Salesforce Leads and Contacts |
| [`node-wire-google-drive`](https://pypi.org/project/node-wire-google-drive/) | `google_drive` | Google Drive files and sharing |
| [`node-wire-fhir-epic`](https://pypi.org/project/node-wire-fhir-epic/) | `fhir_epic` | Epic FHIR R4: patients, encounters and document references |
| [`node-wire-fhir-cerner`](https://pypi.org/project/node-wire-fhir-cerner/) | `fhir_cerner` | Oracle Health (Cerner) FHIR R4: patients, encounters and document references |

Every package is held within the same major version (`>=1.x,<2`), so upgrading this package
never moves you to a new major version of any of them.

## After installing

Installing a connector does not turn it on. Node Wire loads only the connectors named in
`NW_ALLOWED_CONNECTORS`, so list the ones you use:

```bash
export NW_ALLOWED_CONNECTORS=http_generic,slack,stripe
```

Each connector then needs its own entry in `config/connectors.yaml` and its credentials. The
package page of each connector, linked above, lists both. See
[Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Install a subset

Install the connector packages you need by name; each one pulls in `node-wire-runtime`:

```bash
pip install node-wire-smtp node-wire-google-drive
```

## Pre-releases

Pre-releases of this package are published as `X.Y.ZbN` and depend on the pre-releases of the
runtime and connectors. Install one with:

```bash
pip install --pre node-wire-connectors
```

A stable release depends only on stable releases of the runtime and connectors.

## Documentation

- [Node Wire documentation](https://aot-technologies.github.io/node-wire/)
- [Connector catalog](https://aot-technologies.github.io/node-wire/connector-reference/#connector-catalog)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).

<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Exposure profiles

`expose/<connector_id>/<profile>.yaml` lists the actions a deployment exposes. Pick one at run
time with `NW_EXPOSE_PROFILE=<profile>`; with none set, every action is exposed.

```bash
uv run nw expose new github github-readonly -s method:GET   # write a profile
uv run nw expose resolve                                     # refresh after the connector changes
uv run nw expose resolve --check                             # CI: fail when a list is stale
```

Only `actions:` is read at run time. `select:` / `exclude:` are for the CLI.

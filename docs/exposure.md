<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Action exposure profiles

A connector can have hundreds of actions (GitHub has 1,239). An **exposure profile** lets one deployment serve only the actions it needs, on REST, gRPC, MCP and ToolHive (stacklok) alike. With no profile set, every action is exposed, as before.

## How it works

Two moments, kept apart:

- **Authoring time.** The `nw expose` CLI turns selectors (`method:GET`, `path:/repos/*/branches*`, `search:"merge pull request"`) into an exact list of action names, and writes it to `expose/<connector_id>/<profile>.yaml`.
- **Run time.** The server reads only that `actions:` list, as a set. Each call is one lookup. No selector, search or glob is evaluated while serving.

```yaml
connector: github
connector_version: 1.1.0
profile: github-branches
select:
  - path:/repos/*/branches*
  - name:git_*ref*
exclude:
  - method:DELETE
actions:
  - git_create_ref
  - git_get_ref
  - repos_list_branches
```

## Turning it on

Set `NW_EXPOSE_PROFILE` to the profile name. The server loads `expose/<connector_id>/<name>.yaml` for each connector it serves.

| Variable | Meaning | Default |
|---|---|---|
| `NW_EXPOSE_PROFILE` | Profile name (a name, not a path). Unset exposes everything | _(unset)_ |
| `NW_EXPOSE_DIR` | Folder holding `<connector_id>/<profile>.yaml` | `expose/` beside the `config/` folder of `NW_CONFIG_PATH` |
| `NW_EXPOSE_STRICT_VERSION` | `true` refuses to start when a profile was written for another connector version; otherwise it only warns | `false` |

The server **refuses to start** when no loaded connector has the profile's file, the YAML is invalid, the profile names an action the connector doesn't have (the error suggests the closest names), or its `connector:` key doesn't match its folder. A loaded connector with no file for the chosen profile exposes **nothing** and logs a warning (fail closed). The startup log shows what is served:

```text
exposure: profile github-branches
exposure: github: 9 of 1239 actions (DELETE 1, GET 4, PATCH 1, POST 2, PUT 1)
```

## What a hidden action looks like

Hiding is enforced in two places. **Listings** leave it out, so clients never see it: no REST route (and none in `/openapi.json` or the playground), no MCP tool in `tools/list`, no hit in `nw_search_tools`, no tool in a stacklok server. **The call check** in the shared `invoke()` refuses it anyway, before the connector is created, so a guessed name or a stale client cannot reach it.

| Surface | A call to a hidden action |
|---|---|
| REST | 404 (a plain `Not Found` when there is no route; `ACTION_NOT_EXPOSED` from the call check) |
| gRPC | `InvokeResponse` with `success=false`, `error_code=ACTION_NOT_EXPOSED` |
| MCP | `UNKNOWN_TOOL` (it is not listed) |
| ToolHive / stacklok | `ACTION_NOT_EXPOSED` |

See [Errors](errors.md#runtime-wide-codes).

## Authoring with `nw expose`

```bash
uv run nw expose search github "list branches"                  # find actions by keyword
uv run nw expose new github github-readonly -s method:GET -x "path:/repos/*/hooks*"
uv run nw expose new github my-profile                          # on a terminal: prompts for selectors
uv run nw expose resolve                                        # refresh every profile
uv run nw expose resolve --check                                # change nothing; exit 1 if stale
```

| Selector | Picks |
|---|---|
| `name:<glob>` (or a bare word) | Actions by name |
| `path:<glob>` | Actions by their HTTP path. `*` also matches `/` |
| `method:<VERB>` | Actions by HTTP method |
| `tag:<tag>` | Actions by OpenAPI tag. GitHub's generated actions have no tags yet |
| `search:<keywords>` | The 20 best BM25 matches over name, path, tags and description |

A profile is every `select:` match minus every `exclude:` match. A select that matches nothing is an error. Selectors use each action's HTTP method and path, which `nw gen-all` writes into the generated connector; `nw expose` reads them from the connector's source. A profile without `select:` is a hand-written list: `resolve` never rewrites it and only checks that its actions still exist.

!!! warning "Review what a selector picks"
    A path selector can include destructive verbs. Add `exclude:` selectors and read the resulting `actions:` before shipping. For example, a `github-prs` profile built from `path:/repos/*/pulls*` still includes four `DELETE` actions.

## In the build pipeline

- `nw gen-all` re-expands the profiles of the connector it just regenerated (the **Exposure profiles** stage) and reports each file it updated.
- `nw gen-mcp` and `nw docker-build` refuse to build when a profile is stale and tell you to run `nw expose resolve <connector_id>`. `docker-build` also refuses a generated host whose copied profiles differ from `expose/<connector_id>/`; regenerate it with `nw gen-mcp --connector-id <id> --force-output`.
- The generated MCP host and `nw gen-stacklok` servers copy `expose/<connector_id>/` to `/app/expose/` in the image. You still choose the profile at run time with `NW_EXPOSE_PROFILE`; the image default is every action.
- CI runs `nw expose resolve --check`, so a connector upgrade that changes a selector's result fails the build until the profile is re-resolved and reviewed.

## Not covered

- `NW_MCP_TOOL_MODE=auto` is a separate change. A profile applies in both `list` and `search` modes.
- A profile limits which actions are served. It is not authorization: who may call an exposed action is still decided by the scope policy and tenancy.

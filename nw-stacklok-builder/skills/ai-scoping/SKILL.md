---
name: ai-scoping
description: Transform an OpenAPI 3.x spec and workflow descriptions into a validated mcp-scope.yaml that ultimately gets used to generate an MCP server. Guides the AI through spec analysis, semantic grouping, tool naming, description writing, and auth detection with interactive user gates. Use when a user wants to scope an API for MCP server generation.
argument-hint: <openapi-spec-path>
---

# AI Scoping Skill

This skill orchestrates Phase 1 of the mcp-builder pipeline: transforming an OpenAPI spec and workflow descriptions into a validated `mcp-scope.yaml` and a `scoping-summary.md` documenting the AI's reasoning. The `scoping-summary.md` provides a human-readable explanation of the AI's decisions used in Phase 2 for the human to approve the MCP scoping doc. Once approved, the `mcp-scope.yaml` serves as the contract for deterministic code generation in Phase 3 that generates an MCP server.

## Ground rules

These apply throughout the workflow. Violating any of them is a skill failure even if the final output looks correct.

1. **USER GATEs are hard stops.** Steps marked USER GATE (group selection, tool approval, auth detection) end with a question and nothing else. Do not pre-compute the next step's content, do not spawn the next sub-agent, and do not write any file associated with the next step in the same turn. Wait for the user's explicit answer before continuing. Pre-computing corrupts the gate — users cannot cleanly redirect when you have already acted.

2. **Reason from the generator contract, not from memory.** The authoritative description of what the code generator actually does lives at `{skill_base_dir}/assets/generator-contract.md` (`response_kind` behavior, supported auth types, known generator gaps). Before claiming that the generator will produce or reject something, read that file. Do not infer generator behavior from training data or by opening the generator templates directly.

3. **Recap before acting.** Before spawning a sub-agent, running `uv run mcp-builder`, or writing a file in the working directory, state in one sentence what you are about to do. Silent execution hides the decisions that need to be gated.

4. **Flag generator gaps explicitly.** When a natural scoping choice hits a limitation listed in the "Known gaps" section of `generator-contract.md` (streaming, multipart uploads, per-tool base URLs, etc.), surface it to the user with their options — do not flip-flop recommendations or silently drop the tool.

## Startup

Before beginning the workflow:

1. Determine the base directory for this skill (the absolute path to the directory containing this SKILL.md). The repo root is two levels up: `{skill_base_dir}/../..`. Agent files are at `{repo_root}/agents/{agent-name}.md`. Assets are at `{skill_base_dir}/assets/`. Do not share these internal paths with the user.

2. All `uv` and `task` commands must run from `{repo_root}` (where `pyproject.toml` lives).

3. Ensure the JSON schema is current by running from `{repo_root}`:
   ```bash
   uv run mcp-builder-schema
   ```
   This writes `mcp-scope-schema.json` and prints the absolute path. You will need this path for validation in Step 6.

## Workflow

Given an OpenAPI spec path ($ARGUMENTS), execute the following steps:

1. Collect inputs
2. Spec analysis (deterministic extraction + agent)
3. Group selection (user gate)
4. Tool scoping (agent)
5. Tool approval (user gate)
6. Output assembly

---

### Step 1: Collect Inputs

1. Verify the OpenAPI spec file exists at the provided path. If it does not exist, tell the user and exit.

2. Then ask the user for workflow descriptions. **Minimum 1 workflows required.** Workflows describe what users need to accomplish with the API — they should capture different personas or use cases to ensure broad coverage of the API surface. Encourage the user to provide as many distinct workflows as possible (3 is a good target) to improve the quality of the scoping results. But you can proceed with fewer if needed.

3. Ask the user about authentication requirements. Add an auth hint if the user talks about the spec's auth.

4. Create a working directory for intermediate files: `{cwd}/scoping-output-{date_timestamp}/`

---

### Step 2: Spec Analysis

#### 2.1: Run deterministic extraction

Run the CLI to parse the spec into structured JSON. Logs go to stderr, JSON goes to stdout, so redirect directly:

```bash
uv run mcp-builder analyze <spec-path> 2>/dev/null > {working_dir}/analyze.json
```

This outputs JSON with all endpoints, security schemes, and quality metrics.

If the command fails (invalid spec, unsupported format), present the error to the user and exit.

#### 2.2: Resolve URL placeholders and relative URLs

Specs for multi-tenant APIs routinely ship `{placeholder}` literals in `servers[].url` and OAuth URLs (e.g. `https://{companyDomain}.bamboohr.com`). OAuth flows also sometimes declare `authorizationUrl` / `tokenUrl` as server-relative paths (`/authorize.php`), and the analyzer emits `base_url=""` when the spec has no `servers` block. A scope written with any of these shapes propagates verbatim into the generated client and fails at runtime. Resolve all of them here, once, before any downstream step uses the URLs.

1. **Collect URL fields** from `{working_dir}/analyze.json`:
   - `base_url`
   - For each `security_schemes[*]` with `type == "oauth2"`, for each flow in its `flows`: `authorization_url` and `token_url`. Track these per `(scheme_name, flow_name)` so multiple OAuth schemes are not collapsed into a single pair.

2. **Detect issues**:
   - Empty or non-absolute `base_url`: does not start with `http://` or `https://`
   - Balanced `{…}` placeholder: any `re.findall(r"\{[^{}]+\}", url)` match
   - Relative OAuth URL: an `authorization_url` / `token_url` that does not start with `http://` or `https://`

   If none found, skip to Step 6 — persistence still runs with passthrough values so downstream steps can read `resolved_urls` unconditionally.

3. **Gather inference hints** for each placeholder (do not commit yet):
   - Open the raw OpenAPI spec file (from `$ARGUMENTS`). For each placeholder name, look up `servers[*].variables[<name>].default`. This is the standard mechanism spec authors use — treat it as a strong hint.
   - Cross-reference the user's Step 1.3 auth hint and the workflow descriptions for any domain or subdomain the user already mentioned.

4. **Confirm with the user**:
   - If `base_url` is empty or non-absolute, ask the user for the concrete absolute base URL first. All relative OAuth URL resolution below depends on it.
   - Then, for every placeholder (even when a spec-declared default exists), present:
     - The template URL it appears in
     - The placeholder name
     - The spec-declared default (if any)
     - Any cross-referenced hint
     - A request for the concrete value to use

   Do not proceed until the base URL is absolute and every placeholder has a concrete user-supplied value.

5. **Resolve relative OAuth URLs** against the concrete (now placeholder-free) `base_url` using `urllib.parse.urljoin` semantics.

6. **Persist a `resolved_urls` mapping** for the rest of the workflow. Always populate it, even on the no-op path — downstream steps read it unconditionally:
   ```
   resolved_urls = {
     "base_url": "<concrete absolute URL>",
     "oauth": {
       "<scheme_name>": {
         "<flow_name>": {
           "authorization_url": "<concrete absolute URL or None>",
           "token_url": "<concrete absolute URL or None>",
         },
       },
     },
   }
   ```

   Rules for each field:
   - `base_url`: always a string. On the no-op path, copy the already-absolute value from `analyze.json` verbatim.
   - `oauth`: always a dict. If the spec has **no OAuth schemes**, use `{}`. Otherwise include one entry per `(scheme_name, flow_name)` present in the spec.
   - `authorization_url` / `token_url`: strings when the flow declares them (copied verbatim from `analyze.json` on the no-op path, or the resolved value otherwise). Use `None` for a flow that genuinely doesn't declare that URL (e.g. `client_credentials` has no `authorization_url`).

   Example — spec with no OAuth at all:
   ```
   resolved_urls = {"base_url": "https://api.example.com", "oauth": {}}
   ```

   Every downstream step (2.3, 4, 6.1, 6.2, 6.3) uses these values.

#### 2.3: Spawn spec-analyzer agent

Spawn a **spec-analyzer** sub-agent using the Agent tool:

```
Agent tool parameters:
- subagent_type: [path to spec-analyzer agent: {repo_root}/agents/spec-analyzer.md]
- description: "Analyze OpenAPI spec"
- prompt: |
    Analyze the following OpenAPI spec data and propose semantic endpoint groups.

    CONTEXT:
    Pipeline context path: [absolute path to {skill_base_dir}/assets/pipeline-context.md]
    Working directory: [absolute path to {working_dir} from Step 1.4]

    WORKFLOWS:
    1. [workflow 1]
    2. [workflow 2]
    3. [workflow 3]
    ...

    SPEC ANALYSIS JSON PATH:
    [absolute path to {working_dir}/analyze.json]

    RESOLVED BASE URL:
    [resolved_urls["base_url"] from Step 2.2 — the concrete, placeholder-free base URL the agent should display instead of the verbatim value in analyze.json]
- mode: acceptEdits
- run_in_background: false
```

The spec-analyzer agent will:
- Assess spec quality (description coverage, flagged issues)
- Propose semantic endpoint groups based on tags, path prefixes, and domain semantics
- Annotate each group with workflow relevance
- Write `spec-analysis.md` to the working directory

---

### Step 3: Group Selection (USER GATE)

Once the spec-analyzer agent completes, explain to the user that you need help downscoping tool groups, and they can scope individual tools later.

1. Read `{working_dir}/spec-analysis.md`
2. Present the quality report to the user
3. Present all proposed groups in a clear format:
   - Group name and description
   - Number of endpoints in the group
   - Workflow relevance rating (high/medium/low) with brief reason
4. Recommend which groups to include based on workflow alignment (suggest all high-relevance groups, optionally medium)
5. Ask the user which groups to include

**Do NOT proceed to Step 4 until the user has selected their groups.** (See ground rule 1.)

After selection, extract the endpoint details for the selected groups from `spec-analysis.md`. You will pass this filtered data to the endpoint-scoper agent.

---

### Step 4: Tool Scoping

Spawn an **endpoint-scoper** sub-agent using the Agent tool:

```
Agent tool parameters:
- subagent_type: [path to endpoint-scoper agent: {repo_root}/agents/endpoint-scoper.md]
- description: "Scope tools for selected groups"
- prompt: |
    Perform tool scoping for the selected endpoint groups: naming, descriptions, hints.

    CONTEXT:
    Pipeline context path: [absolute path to {skill_base_dir}/assets/pipeline-context.md]
    Generator contract path: [absolute path to {skill_base_dir}/assets/generator-contract.md]
    Working directory: [absolute path to {working_dir} from Step 1.4]
    Server name: [derived from API — e.g., "google-drive"]
    Base URL: [resolved_urls["base_url"] from Step 2.2 — the concrete, placeholder-free base URL, e.g., "https://www.googleapis.com/drive/v3"]
    OpenAPI spec file path: [absolute path to the downloaded OpenAPI spec file]

    WORKFLOWS:
    1. [workflow 1]
    2. [workflow 2]
    3. [workflow 3]
    ...

    SPEC ANALYSIS PATH:
    [absolute path to {working_dir}/spec-analysis.md]

    SELECTED GROUPS:
    [comma-separated list of selected group names]
- mode: acceptEdits
- run_in_background: false
```

The endpoint-scoper agent will:
- Flag questionable endpoints for user review (does NOT auto-remove anything)
- Assign tool names — keeping originals when possible, renaming only bad ones
- Write LLM-optimized descriptions focused on separability between tools
- Add hints for pagination, large responses, quirks
- Set each tool's `response_kind` per the rules in `{skill_base_dir}/assets/generator-contract.md` (section "Picking a kind from the spec"), and flag any endpoint whose 2xx responses mix incompatible media types for the user's decision
- Write `tool-scoping.md` to the working directory

---

### Step 5: Tool Approval (USER GATE)

Once the endpoint-scoper agent completes:

1. Read `{working_dir}/tool-scoping.md`
2. Present to the user:
   - **Flagged endpoints**: which endpoints are flagged (exclusion candidates, mixed-media endpoints, etc.) and the options the scoper recorded for each. The user decides per endpoint.
   - **Tool list**: for each tool, show the tool name, endpoint, description, parameters, `response_kind`, and hints
   - **Renamed tools**: highlight any tools where the name was changed from the original operationId
3. Ask the user to approve the tool list or request changes (including which flagged endpoints to remove, if any)
4. Do a final audit of the user tool selections for consistency. Make sure tools that need to appear together are all selected or that the user understands the implications of removing certain tools (e.g., if they remove an endpoint that is a prerequisite for another tool, flag that for review).

**Do NOT proceed to Step 6 until the user approves.** (See ground rule 1.)

If the user requests changes:
- For minor edits (rename a tool, tweak a description), apply them directly
- For significant rework (add/remove multiple tools, restructure groups), re-spawn the endpoint-scoper agent with updated instructions

---

### Step 6: Output Assembly

#### 6.1: Auth Detection

Read the `security_schemes` from `{working_dir}/analyze.json` and map to MCPScope auth types:

| OpenAPI Security Scheme | MCPScope `auth.type` | Notes |
|------------------------|---------------------|-------|
| `openIdConnect` (with `openIdConnectUrl`) | `oidc` | Use the `openIdConnectUrl`'s origin as `issuer`. Fetch `/.well-known/openid-configuration` to pull scopes into `scopes_available`. |
| `oauth2` (authorization code flow) with a known-OIDC provider (Google, Atlassian, Okta, etc.) | `oidc` | Only if the provider publishes `/.well-known/openid-configuration` — verify by fetching it. |
| `oauth2` (authorization code flow) otherwise | `oauth2` | Use `resolved_urls["oauth"][<selected_scheme>][<selected_flow>]["authorization_url"]` and `token_url` from Step 2.2 — never the raw analyze.json values. These are already absolute (placeholders substituted, relative paths resolved). Extract scopes from the flow definition. |
| `http` (bearer) | `api_key` | Static bearer-token pattern; API key in the `Authorization` header. |
| `apiKey` (header: `X-API-Key`, `Authorization`) | `api_key` | |
| `apiKey` (query parameter) | `none` | **Not supported** — flag in notes |
| `http` (basic) | `none` | **Not supported** — flag in notes |
| No security schemes | `none` | |

If multiple security schemes exist, select the most ToolHive-compatible one and document alternatives in `auth.notes`.

**Pick `oauth2` over `oidc` when uncertain.** OIDC is a specialization of OAuth2 — if the spec declares `oauth2` and you cannot confirm OIDC discovery-doc compliance, stay with `oauth2`. Inventing an `issuer` for a non-OIDC provider (as the old `oauth_bearer` type did) is what this schema is designed to prevent.

**Scopes — always populate `scopes_available` from the spec.** Copy every scope declared under the OAuth2 flow or the OIDC discovery document into `scopes_available` as a dict of `{scope_name: description}` — do not curate it. Then produce `scopes_required` as the minimal subset needed for the tools in the scope. The full catalog lets downstream reviewers pick different scopes without re-reading the spec.

**URL sourcing.** All OAuth endpoint URLs (`authorization_url`, `token_url`) come from `resolved_urls` in Step 2.2, which has already handled placeholder substitution and relative-path resolution. Never emit relative endpoint URLs or raw `{placeholder}` literals.

**`userinfo_url` for OAuth2 (optional).** OpenAPI has no standard field for this. If the API's OAuth docs declare one, populate it; otherwise ask the user once — if they don't know, omit the field. Do not invent a URL.

**Discovery-doc conformance check for OIDC (soft warning):** When `auth.type` is `oidc` and the issuer URL is resolvable, fetch `{issuer}/.well-known/openid-configuration` and confirm these fields are present: `response_types_supported`, `id_token_signing_alg_values_supported`, `subject_types_supported`, `authorization_endpoint`, `token_endpoint`, `jwks_uri`. If any are missing, the provider probably is not genuinely OIDC — downgrade to `oauth2`, populate the endpoint URLs from the discovery doc, and note the downgrade. Template placeholders in the issuer (e.g. `{companyDomain}`) also block this check — note it and keep the placeholder.

Do not run this check for `oauth2` — there is no discovery doc to probe.

**USER GATE:** Present the auth detection result to the user (selected type, endpoints or issuer, selected scopes from the full catalog, any discovery-doc warning, and any alternatives you rejected). **Do NOT proceed to Step 6.2 until the user confirms the auth block.** (See ground rule 1.) This gate is easy to skip by accident — do not.

#### 6.2: Determine Server Metadata

Derive the following from the analyze JSON and user context:
- `server.name`: DNS-label version of the API name (lowercase, hyphens, e.g., `google-drive`). Must match regex `[a-z0-9]([a-z0-9-]*[a-z0-9])?` and be <=63 chars.
- `server.description`: One-line description of what the MCP server does
- `spec.source`: The original spec path or URL
- `spec.format`: `openapi3` or `openapi3.1` (from analyze JSON)
- `spec.base_url`: `resolved_urls["base_url"]` from Step 2.2 (concrete, placeholder-free, absolute). Never the raw analyze JSON value.
- `spec.total_endpoints`: Total count from analyze JSON
- `spec.scoped_endpoints`: Count of tools in the approved list

#### 6.3: Assemble `mcp-scope.yaml`

Build the YAML following the MCPScope schema exactly. The formal JSON schema was generated when you ran `uv run mcp-builder-schema`.

All URL fields (`spec.base_url`, `auth.oauth.issuer`) use the resolved values from Step 2.2. The assembled YAML must never contain an unresolved `{…}` placeholder or a relative URL.

```yaml
version: "1"

server:
  name: {server_name}
  description: "{description}"

spec:
  source: "{source}"
  format: {format}
  base_url: "{base_url}"
  total_endpoints: {total}
  scoped_endpoints: {scoped}

workflows:
  - "{workflow 1}"
  - "{workflow 2}"
  - "{workflow 3}"

groups:
  - name: {group-name}
    description: "{group description}"
    tools:
      - tool_name: {name}
        endpoint: {METHOD} {/path}
        description: >
          {LLM-optimized description}
        response_kind: {json|text|binary|auto}
        parameters:
          - name: {param_name}
            description: "{param description}"
            required: {true|false}
            location: {path|query|body}
        hints:
          - "{hint}"

# Auth takes one of four discriminated-union shapes. Emit ONLY the fields
# listed for the selected type — extra fields are rejected at load time.
#
# Option A: OIDC (upstream publishes a discovery document)
auth:
  type: oidc
  issuer: "{issuer_url}"
  scopes_available:
    "{scope_name}": "{description}"
    # ... full catalog from the OIDC discovery document
  scopes_required:
    - {scope}
  notes: >
    {auth notes}

# Option B: OAuth2 (no OIDC discovery; endpoints declared inline)
auth:
  type: oauth2
  flow: authorizationCode
  authorization_url: "{absolute_authorization_url}"
  token_url: "{absolute_token_url}"
  userinfo_url: "{absolute_userinfo_url}"   # optional; omit if unknown
  scopes_available:
    "{scope_name}": "{description}"
    # ... full catalog from the OpenAPI oauth2 flow
  scopes_required:
    - {scope}
  notes: >
    {auth notes}

# Option C: API key (static bearer token in the Authorization header)
auth:
  type: api_key
  notes: >
    {auth notes}

# Option D: no auth
auth:
  type: none
```

Copy each tool's `response_kind` from the approved `tool-scoping.md`
output — Step 4 already resolved it (including any mixed-media
decisions surfaced during the Step 5 user gate).

Write the YAML to `{working_dir}/mcp-scope.yaml`.

#### 6.4: Validate

Run validation from the working directory:

```bash
uv run mcp-builder validate {working_dir}/mcp-scope.yaml --openapi-spec <openapi-spec-path>
```

This checks:
- Schema compliance (tool names unique, snake_case, <=40 chars; server name is DNS label; auth config valid; path params declared; `response_kind` set)
- Cross-validation (every endpoint in the scope exists in the spec's paths)
- Parameter coverage (every YAML parameter exists in the spec — warnings flag params missing from the spec whose types will default to `str`, which usually means the spec is incomplete)
- Response-kind compatibility (scope's `response_kind` must match what the spec actually declares — e.g. `response_kind: json` against a PDF-only endpoint errors; `response_kind: binary` against a `text/plain`-only endpoint errors because it would base64-wrap readable text)

If validation fails or warns:
1. Read the error/warning output
2. **Errors**: fix the YAML (common issues: tool name too long, endpoint path doesn't match spec, missing required auth config, path parameter not declared, `response_kind` mismatches what the spec declares). Present the error to the user and follow their decision.
3. **Warnings about missing spec parameters**: **STOP and present these to the user before continuing.** These warnings mean the OpenAPI spec does not define these parameters, so codegen will default their types to `str`. This is often because the spec is incomplete (e.g., no `requestBody` on a POST endpoint). The user must confirm this is acceptable. If the param name is simply misspelled vs the spec, fix it and re-validate.
4. Re-validate after fixes
5. Repeat up to 3 times. If still failing after 3 attempts, present the errors to the user and ask for help.

#### 6.5: Write Scoping Summary

Read the template at `{skill_base_dir}/assets/scoping-summary-template.md` and fill it in with:
- Quality report from spec-analysis.md
- Group inclusion/exclusion decisions and rationale
- Tool name changes (original -> new) with rationale
- Inferred descriptions flagged for review
- Auth detection reasoning
- Any flagged issues for Phase 2 human review

Write the filled template to `{working_dir}/scoping-summary.md`.

#### 6.6: Present Results

Read `{working_dir}/scoping-summary.md` and present the user with a comprehensive completion summary:

**What was generated:**
- Server name and description
- N tools across M groups (list each group name with its tool count)
- Auth type detected (with issuer if OAuth)

**Key decisions made:**
- Tools renamed from their original operationId (original → new, with one-line rationale), or "No tools renamed" if all kept originals
- Endpoints that were flagged and their disposition (kept or excluded)
- Descriptions that were inferred (not sourced from the spec) and should be reviewed for accuracy

**Parameter highlights:**
- For each tool with explicit parameters in the YAML: note how many parameters were included vs. how many the spec defines, and list any excluded parameters
- Flag any tools where all spec parameters were kept without curation

**Phase 2 review checklist:**
- Pull the "Flagged for Phase 2 Review" checklist items from `scoping-summary.md` and present them inline so the user sees what needs attention
- Remind the user that Phase 2 (human review) should review the YAML before code generation

**Output files:**
- Path to `mcp-scope.yaml`
- Path to `scoping-summary.md`

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| `mcp-builder analyze` fails | Present error to user, exit |
| Spec has 500+ endpoints | Proceed normally — spec-analyzer handles large catalogs via grouping |
| Spec has no descriptions | Proceed — endpoint-scoper infers descriptions and flags 100% for review |
| Auth scheme unsupported | Set auth type to `none`, flag in notes and scoping summary |
| Validation fails after 3 retries | Present errors to user, ask for manual fix |
| User rejects groups/tools repeatedly | Apply changes and re-present; do not loop indefinitely |

## Important Notes

### Sub-Agent Invocation
- Use the Agent tool with the appropriate subagent_type to spawn sub-agents
- Always set `run_in_background: false` and `mode: acceptEdits` so agents can interact with the user if needed
- Pass all context in the prompt CONTEXT section — agents do not share memory with the orchestrator

### Working Directory
- All files (intermediate and final) go in `{cwd}/scoping-output-{date_timestamp}/` (see Step 1.4). Use today's date as the timestamp to avoid collisions across runs.
- This includes `analyze.json`, `spec-analysis.md`, `tool-scoping.md`, `mcp-scope.yaml`, and `scoping-summary.md`

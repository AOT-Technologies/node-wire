#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Build, deploy and check the ToolHive runbook's MCP servers (Petstore, Slack) on this machine.

Local only: needs Docker, ToolHive (`thv`) and a `docker login dhi.io` (stacklok's base image).

  1. Builds with the real `nw` CLI, in parallel lanes. Shared wheels are built once, and a
     package whose sources are unchanged since its last build is reused (gen-all's stamps).
  2. Deploys each MCP server behind ToolHive under the runbook's workload names, so they can be
     called by hand afterwards.
  3. Checks each server's tool listing through ToolHive (`thv mcp list tools`), and the error
     taxonomy by calling tools through the ToolHive proxy with inputs that fail before any
     upstream request.

Tokens (PET_STORE_API_KEY, PET_STORE_ACCESS_TOKEN, SLACK_WEB_ACCESS_TOKEN) are optional. When
set they are handed to the servers (through 0600 files, never on a command line) and no tool that
would reach Petstore or Slack is called. When unset the servers still list their tools, and the
checks confirm that actions fail for want of a credential.

Usage:
  uv run python scripts/e2e_toolhive.py               # build, deploy, check
  uv run python scripts/e2e_toolhive.py --skip-build  # redeploy and recheck the built images
  uv run python scripts/e2e_toolhive.py --clean       # stop and remove everything it started

  # With telemetry: start grafana/ (see its README), then point the servers at its collector.
  # Also checks that each failed call's trace_id leads to its log line in Grafana (Loki).
  uv run python scripts/e2e_toolhive.py --otlp-endpoint http://host.docker.internal:4318
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404  # fixed argv lists, no shell
import sys
import threading
import time
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import httpx
import yaml
from mcp_http import McpSession, wait_for

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = REPO_ROOT / ".e2e-toolhive"
FIXTURES = REPO_ROOT / "tests" / "nw_stacklok_builder" / "fixtures" / "stacklok"
PETSTORE_SCOPE = FIXTURES / "petstore.yaml"
SLACK_SCOPE = FIXTURES / "slack_post.yaml"

PETSTORE_V2 = "https://petstore.swagger.io/v2/swagger.json"
SLACK_SPEC = (
    "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/"
    "slack_web_openapi_v2.json"
)
TOKEN_VARS = ("PET_STORE_API_KEY", "PET_STORE_ACCESS_TOKEN", "SLACK_WEB_ACCESS_TOKEN")

# ToolHive publishes a workload's --target-port on the host too, so host workloads running side
# by side each need their own (the image listens on NW_MCP_PORT, default 8081).
HOST_PORTS = {
    "pet-store-nw-mcp": 18081,
    "pet-store-nw-mcp-search": 18082,
    "slack-web-nw-mcp": 18083,
    "slack-web-nw-mcp-search": 18084,
    "pet-store-nw-mcp-mt": 18085,
}
STACKLOK_PORT = 8100  # the stacklok server image
TENANT_TOOLS = {"nw_list_tenants", "nw_select_tenant", "nw_list_configs", "nw_select_config"}
CONFIG_TOOLS = {"nw_list_configs", "nw_select_config"}
SEARCH_TOOLS = {"nw_search_tools", "nw_call_tool"}

# stacklok servers: NodeWireToolError text, after FastMCP's "Error executing tool <name>: ".
_STACKLOK_ERROR = re.compile(
    r"(?P<code>[A-Za-z][A-Za-z0-9_]*) \[(?P<category>RETRYABLE|BUSINESS|AUTH|FATAL)\]: "
    r".*?(?: \(trace_id=(?P<trace_id>[0-9a-f-]{36})\))?$",
    re.S,
)


# ---------------------------------------------------------------------------------------------
# Build: jobs of `nw` commands, run in parallel as their dependencies finish
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    name: str
    argv: tuple[str, ...]
    after: tuple[str, ...] = ()


def _nw(*args: str) -> tuple[str, ...]:
    return ("uv", "run", "nw", *args)


def _host_project(connector_id: str) -> str:
    return f"nw-mcp-builder/out/{connector_id.replace('_', '-')}-nw-mcp"


def _host_lane(connector_id: str, first: str) -> List[Job]:
    """gen-mcp and docker-build for the full list, then for tool search, one after the other.

    Both modes write the same project folder, so each image is built before it is replaced.
    """
    project = _host_project(connector_id)
    full, search = f"{connector_id}:gen-mcp-full", f"{connector_id}:gen-mcp-search"
    return [
        Job(
            full,
            _nw("gen-mcp", "--connector-id", connector_id, "--full-tool-list", "--force-output"),
            (first,),
        ),
        Job(
            f"{connector_id}:image-latest",
            _nw("docker-build", "--project", project, "--tag", "latest"),
            (full,),
        ),
        Job(
            search,
            _nw("gen-mcp", "--connector-id", connector_id, "--tool-search", "--force-output"),
            (f"{connector_id}:image-latest",),
        ),
        Job(
            f"{connector_id}:image-search",
            _nw("docker-build", "--project", project, "--tag", "search"),
            (search,),
        ),
    ]


def build_jobs() -> List[Job]:
    """The build graph. What may not overlap, and why:

    - Wiring (config/connectors.yaml, sample.env, build-packages.sh) has one writer at a time:
      the two codegen jobs, then each gen-stacklok in turn. The lanes build with --no-wire.
    - Two wheel builds of one package never overlap: both write its Cython .c files in src/.
      The shared glibc wheels are built once, in pet_store:wheels; stacklok's Alpine ones in
      petstore3:gen-stacklok, which slack_web:gen-stacklok waits for.
    - gen-stacklok regenerates its connector, so slack_web's runs after that connector's
      host builds have copied its wheel.
    """
    jobs = [
        Job(
            "slack_web:codegen",
            _nw(
                "gen-all",
                "--connector-id",
                "slack_web",
                "--path",
                SLACK_SPEC,
                "--force",
                "--no-wheel",
                "--no-mcp",
            ),
        ),
        Job(
            "pet_store:wheels",
            _nw(
                "gen-all",
                "--connector-id",
                "pet_store",
                "--path",
                PETSTORE_V2,
                "--force",
                "--no-mcp",
            ),
            ("slack_web:codegen",),
        ),
        Job(
            "slack_web:wheels",
            _nw(
                "gen-all",
                "--connector-id",
                "slack_web",
                "--path",
                SLACK_SPEC,
                "--force",
                "--no-mcp",
                "--no-wire",
            ),
            ("pet_store:wheels",),
        ),
        Job(
            "petstore3:gen-stacklok",
            _nw(
                "gen-stacklok",
                "--scope",
                str(PETSTORE_SCOPE),
                "--connector-id",
                "petstore3",
                "--force",
            ),
            ("pet_store:wheels",),
        ),
        Job(
            "petstore3:image",
            _nw("docker-build", "--project", "nw-stacklok-builder/out/petstore-mcp"),
            ("petstore3:gen-stacklok",),
        ),
        *_host_lane("pet_store", "pet_store:wheels"),
        *_host_lane("slack_web", "slack_web:wheels"),
        Job(
            "slack_web:gen-stacklok",
            _nw(
                "gen-stacklok",
                "--scope",
                str(SLACK_SCOPE),
                "--connector-id",
                "slack_web",
                "--force",
            ),
            ("slack_web:gen-mcp-search", "petstore3:gen-stacklok"),
        ),
        Job(
            "slack_web:stacklok-image",
            _nw("docker-build", "--project", "nw-stacklok-builder/out/slack-post-mcp"),
            ("slack_web:gen-stacklok",),
        ),
    ]
    names = {job.name for job in jobs}
    unknown = {dep for job in jobs for dep in job.after} - names
    assert not unknown, f"build graph refers to unknown jobs: {sorted(unknown)}"
    return jobs


# What gen-all / gen-stacklok wire each connector into. The built projects keep their own copy,
# so these are put back after the build: the repo's checks (the packaging inventory, ruff) stay
# green and `git status` shows only the generated, gitignored connectors.
WIRING_FILES = ("config/connectors.yaml", "sample.env", "scripts/build-packages.sh")


@contextmanager
def restored(files: Iterable[str]) -> Iterator[None]:
    """Put ``files`` back as they were on entry, even when the build fails or is interrupted."""
    saved = {path: path.read_bytes() for path in (REPO_ROOT / f for f in files) if path.is_file()}
    try:
        yield
    finally:
        for path, content in saved.items():
            if path.read_bytes() != content:
                path.write_bytes(content)


def _build_env() -> Dict[str, str]:
    """The builds need no credentials; keep them out of every build log."""
    return {k: v for k, v in os.environ.items() if k not in TOKEN_VARS}


def run_jobs(jobs: List[Job], *, workers: int, log_dir: Path) -> Dict[str, str]:
    """Run ``jobs`` as their dependencies succeed; returns ``name -> ok | failed | skipped``."""
    log_dir.mkdir(parents=True, exist_ok=True)
    env = _build_env()
    started = time.monotonic()
    status: Dict[str, str] = {}
    pending = {job.name: job for job in jobs}

    def say(mark: str, name: str, note: str = "") -> None:
        minutes, seconds = divmod(int(time.monotonic() - started), 60)
        print(f"  [{minutes:3d}m{seconds:02d}s] {mark} {name}{note}", flush=True)

    def run(job: Job) -> int:
        say("…", job.name)
        with (log_dir / f"{job.name.replace(':', '-')}.log").open("w", encoding="utf-8") as log:
            log.write("$ " + " ".join(job.argv) + "\n\n")
            log.flush()
            return subprocess.run(  # nosec B603  # fixed argv
                job.argv,
                cwd=REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode

    with ThreadPoolExecutor(max_workers=workers) as pool:
        running: Dict[Future[int], tuple[Job, float]] = {}
        while pending or running:
            for name, job in list(pending.items()):
                if any(status.get(dep) in ("failed", "skipped") for dep in job.after):
                    status[name] = "skipped"
                    del pending[name]
                    say("–", name, "  (skipped: a step it needs failed)")
                elif all(status.get(dep) == "ok" for dep in job.after):
                    running[pool.submit(run, job)] = (job, time.monotonic())
                    del pending[name]
            if not running:
                continue  # only skips were decided this pass; look again
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                job, began = running.pop(future)
                ok = future.result() == 0
                status[job.name] = "ok" if ok else "failed"
                took = time.monotonic() - began
                say("✓" if ok else "✗", job.name, f"  ({took:.0f}s)")
    return status


# ---------------------------------------------------------------------------------------------
# Deploy: ToolHive workloads (and the stacklok containers behind them)
# ---------------------------------------------------------------------------------------------


def _cmd(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # nosec B603  # fixed argv
        argv, capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def _private_file(name: str, text: str) -> Path:
    """A 0600 file under .e2e-toolhive/secrets/ (tokens never go on a command line)."""
    directory = WORK_DIR / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / name
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(text, encoding="utf-8")
    return path


def _remove_workload(name: str) -> None:
    _cmd("thv", "stop", name, check=False)
    _cmd("thv", "rm", name, check=False)


def run_host_workload(
    name: str,
    image: str,
    env: Dict[str, str],
    *,
    mounts: Iterable[str] = (),
    port: Optional[int] = None,
) -> None:
    """``thv run`` a node-wire MCP host image with the runbook's settings.

    ``port`` (default: the scenario's in ``HOST_PORTS``) is both ToolHive's target port and the
    server's ``NW_MCP_PORT``; workloads running side by side need different ones.
    """
    port = port or HOST_PORTS[name]
    _remove_workload(name)
    settings = {
        "NW_MCP_HOST": "0.0.0.0",  # nosec B104  # inside the container, for ToolHive's proxy
        "NW_MCP_AUTH_DISABLED": "true",
        "NW_MCP_SCOPE_POLICY_DEFAULT": "allow",
        "NW_MCP_PORT": str(port),
        **env,
    }
    env_file = _private_file(f"{name}.env", "".join(f"{k}={v}\n" for k, v in settings.items()))
    argv = ["thv", "run", "--name", name, "--transport", "streamable-http"]
    argv += ["--target-port", str(port), "--env-file", str(env_file)]
    for mount in mounts:
        argv += ["-v", mount]
    _cmd(*argv, image)


def run_stacklok_container(
    container: str,
    image: str,
    port: int,
    *,
    require_bearer: bool,
    tenants_file: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    """``docker run`` a stacklok server on ``127.0.0.1:port``; returns its MCP URL once up."""
    _cmd("docker", "rm", "-f", container, check=False)
    argv = ["docker", "run", "-d", "--name", container, "-p", f"127.0.0.1:{port}:{STACKLOK_PORT}"]
    argv += ["-e", f"NW_MULTITENANCY_ENABLED={'true' if tenants_file else 'false'}"]
    if not require_bearer:
        argv += ["-e", "REQUIRE_BEARER_TOKEN=false"]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    if tenants_file:
        argv += ["-v", f"{tenants_file}:/app/tenants/tenants.yaml:ro"]
    _cmd(*argv, image)
    url = f"http://127.0.0.1:{port}/mcp"
    with httpx.Client(timeout=10) as client:
        wait_for(client, url, timeout=90)
    return url


def run_remote_workload(
    name: str, url: str, *, token: Optional[str], tenant: Optional[str] = None
) -> None:
    """``thv run`` a ToolHive proxy in front of a stacklok server (a tenant proxy with ``tenant``)."""
    _remove_workload(name)
    argv = ["thv", "run", url, "--name", name, "--transport", "streamable-http"]
    if token:
        argv += ["--remote-auth-bearer-token-file", str(_private_file(f"{name}.token", token))]
    if tenant:
        argv += ["--remote-forward-headers", f"X-Tenant-ID={tenant}"]
    _cmd(*argv)


def workload_url(name: str, timeout: float = 90) -> str:
    """The ToolHive proxy URL of a running workload."""
    deadline = time.monotonic() + timeout
    while True:
        rows = json.loads(_cmd("thv", "list", "--format", "json").stdout or "[]")
        row = next((r for r in rows if r.get("name") == name), None)
        if row and row.get("status") == "running" and row.get("url"):
            return str(row["url"])
        if time.monotonic() > deadline:
            raise RuntimeError(f"ToolHive workload {name} is not running: {row}")
        time.sleep(2)


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------


@dataclass
class Result:
    scenario: str
    check: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    results: List[Result] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, scenario: str, check: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            self.results.append(Result(scenario, check, ok, detail))

    def run(self, scenario: str, check: str, fn: Callable[[], Optional[str]]) -> None:
        """``fn`` returns None on success or the failure's reason."""
        try:
            problem = fn()
        except Exception as exc:  # noqa: BLE001 — a check that crashes is a failed check
            problem = f"{type(exc).__name__}: {exc}"
        self.add(scenario, check, problem is None, problem or "")


def listed_tools(workload: str, timeout: float = 90) -> set[str]:
    """Tool names, as ``thv mcp list tools`` reports them for a ToolHive workload."""
    deadline = time.monotonic() + timeout
    while True:
        proc = _cmd(
            "thv", "mcp", "list", "tools", "--server", workload, "--format", "json", check=False
        )
        if proc.returncode == 0:
            doc = json.loads(proc.stdout)
            tools = doc.get("tools", doc) if isinstance(doc, dict) else doc
            return {t["name"] for t in tools or []}
        if time.monotonic() > deadline:
            raise RuntimeError(f"thv mcp list tools --server {workload}: {proc.stderr.strip()}")
        time.sleep(2)


def _same_tools(listed: set[str], expected: set[str]) -> Optional[str]:
    if listed == expected:
        return None
    return f"missing {sorted(expected - listed)[:8]}, extra {sorted(listed - expected)[:8]}"


def call_tool(url: str, tool: str, arguments: Dict[str, Any]) -> tuple[bool, str]:
    """Call ``tool`` through a ToolHive proxy; returns ``(failed, text)``.

    A node-wire host returns a failed run as its ConnectorResponse envelope (``success: false``,
    its documented output schema), not as ``isError``; both count as failed.
    """
    with httpx.Client(timeout=60) as client:
        session = McpSession(client, url, {}).open()
        reply = session.rpc("tools/call", {"name": tool, "arguments": arguments})
    if "error" in reply:  # a JSON-RPC error rather than a tool result
        return True, json.dumps(reply["error"])
    result = reply["result"]
    text = "".join(c.get("text", "") for c in result.get("content", []))
    return bool(result.get("isError")) or _envelope(text).get("success") is False, text


def _envelope(text: str) -> Dict[str, Any]:
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@dataclass(frozen=True)
class Taxonomy:
    code: Optional[str]
    category: Optional[str]
    trace_id: Optional[str]


def taxonomy(text: str) -> Taxonomy:
    """The error code, category and trace id of a failed call (host envelope or stacklok text)."""
    body = _envelope(text)
    if body.get("success") is False:
        return Taxonomy(body.get("error_code"), body.get("error_category"), body.get("trace_id"))
    match = _STACKLOK_ERROR.search(text.strip())
    if match:
        return Taxonomy(match["code"], match["category"], match["trace_id"])
    return Taxonomy(None, None, None)


def expect_error(
    url: str,
    tool: str,
    arguments: Dict[str, Any],
    expected: tuple[str, str],
    *,
    seen: Optional[List[tuple[str, str]]] = None,
    mentions: str = "",
) -> Optional[str]:
    """The call fails with ``expected`` (code, category), and a trace id to find it in the logs.

    ``seen`` collects ``(code, trace_id)``, for the telemetry check to look up in Grafana;
    ``mentions`` is text the message must contain.
    """
    failed, text = call_tool(url, tool, arguments)
    code, category = expected
    if not failed:
        return f"succeeded; expected {code} [{category}]: {text[:200]}"
    got = taxonomy(text)
    if (got.code, got.category) != expected:
        return f"expected {code} [{category}], got: {text[:300]}"
    if not got.trace_id:
        return f"{code} [{category}] has no trace_id: {text[:300]}"
    if mentions not in text:
        return f"{code} [{category}] does not say {mentions!r}: {text[:300]}"
    if seen is not None:
        seen.append((code, got.trace_id))
    return None


def expect_result(url: str, tool: str, arguments: Dict[str, Any], *needles: str) -> Optional[str]:
    """The call succeeds and its result mentions every one of ``needles``."""
    is_error, text = call_tool(url, tool, arguments)
    if is_error:
        return f"failed: {text[:300]}"
    missing = [n for n in needles if n not in text]
    return f"result lacks {missing}: {text[:300]}" if missing else None


def expect_401(url: str) -> Optional[str]:
    """A request straight to the server, without ToolHive's bearer token, is refused."""
    status = httpx.post(url, json={}, timeout=10).status_code
    return None if status == 401 else f"HTTP {status}, expected 401"


def _host_tools(connector_id: str) -> set[str]:
    from verify_mcp_tools_list import expected_tool_names

    return expected_tool_names(
        connector_id, REPO_ROOT / "src" / f"node_wire_{connector_id}" / "logic.py"
    )


def _scope_tools(scope: Path) -> set[str]:
    doc = yaml.safe_load(scope.read_text(encoding="utf-8"))
    return {t["tool_name"] for g in doc["groups"] for t in g["tools"]}


# ---------------------------------------------------------------------------------------------
# Scenarios: Petstore / Slack x node-wire host / tool search / stacklok, plus tenants + configs
# ---------------------------------------------------------------------------------------------


# Errors raised before any upstream request, as (code, category).
# Documented (docs/stacklok-mcp-servers.md, "Errors and logs"): every failed stacklok tool call
# reads `CODE [CATEGORY]: message (trace_id=...)`.
VALIDATION = ("VALIDATION_ERROR", "BUSINESS")
UNKNOWN_TOOL = ("UNKNOWN_TOOL", "BUSINESS")
MISSING_TENANT = ("MISSING_TENANT", "AUTH")
TENANT_NOT_ALLOWED = ("TENANT_NOT_ALLOWED", "AUTH")
CONFIG_NOT_FOUND = ("CONFIG_NOT_FOUND", "AUTH")
# A missing credential: the host lacks a configured secret (a server misconfiguration), or
# ToolHive forwarded no bearer token to the stacklok server (the caller's authentication).
MISSING_SECRET = ("SECRET_NOT_FOUND", "FATAL")
MISSING_UPSTREAM_TOKEN = ("UPSTREAM_TOKEN_MISSING", "AUTH")


SCENARIO_TITLES = {
    "1": "1 Petstore host",
    "2": "2 Petstore tool search",
    "3": "3 Petstore stacklok",
    "4": "4 Slack host",
    "5": "5 Slack tool search",
    "6": "6 Slack stacklok (posting)",
    "7": "7 Petstore host, tenants",
    "8": "8 Petstore stacklok, tenants",
}


@dataclass
class Context:
    tokens: Dict[str, str]
    report: Report
    otlp_endpoint: Optional[str] = None  # an OTLP/HTTP collector the containers can reach
    _errors: Dict[str, List[tuple[str, str]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def has(self, var: str) -> bool:
        return var in self.tokens

    def seen(self, scenario: str) -> List[tuple[str, str]]:
        """The (code, trace_id) of each failed call ``scenario`` checked."""
        with self._lock:
            return self._errors.setdefault(scenario, [])

    def telemetry(self) -> Dict[str, str]:
        """Server env that turns on OpenTelemetry export (off unless an endpoint is set)."""
        return {"OTEL_EXPORTER_OTLP_ENDPOINT": self.otlp_endpoint} if self.otlp_endpoint else {}


def _host_env(ctx: Context, *keys: str) -> Dict[str, str]:
    return {**ctx.telemetry(), **{k: ctx.tokens[k] for k in keys if ctx.has(k)}}


def scenario_1(ctx: Context) -> None:
    s, name = SCENARIO_TITLES["1"], "pet-store-nw-mcp"
    run_host_workload(
        name,
        "pet-store-nw-mcp:latest",
        _host_env(ctx, "PET_STORE_API_KEY", "PET_STORE_ACCESS_TOKEN"),
    )
    r = ctx.report
    r.run(
        s,
        "lists every pet_store tool",
        lambda: _same_tools(listed_tools(name), _host_tools("pet_store")),
    )
    url = workload_url(name)
    # Argument and tool-name checks run before the connector: plain text, no error code.
    r.run(
        s,
        "unknown tool",
        lambda: expect_error(url, "pet_store_no_such_tool", {}, UNKNOWN_TOOL, seen=ctx.seen(s)),
    )
    r.run(
        s,
        "invalid arguments",
        lambda: expect_error(
            url,
            "pet_store_find_pets_by_status",
            {"status": 5},
            VALIDATION,
            seen=ctx.seen(s),
            mentions="is not of type 'array'",
        ),
    )
    if not ctx.has("PET_STORE_ACCESS_TOKEN"):
        r.run(
            s,
            "no token: petstore_auth action fails",
            lambda: expect_error(
                url,
                "pet_store_find_pets_by_status",
                {"status": ["available"]},
                MISSING_SECRET,
                seen=ctx.seen(s),
            ),
        )
    if not ctx.has("PET_STORE_API_KEY"):
        r.run(
            s,
            "no token: api_key action fails",
            lambda: expect_error(
                url, "pet_store_get_inventory", {}, MISSING_SECRET, seen=ctx.seen(s)
            ),
        )


def scenario_2(ctx: Context) -> None:
    s, name = SCENARIO_TITLES["2"], "pet-store-nw-mcp-search"
    run_host_workload(
        name,
        "pet-store-nw-mcp:search",
        _host_env(ctx, "PET_STORE_API_KEY", "PET_STORE_ACCESS_TOKEN"),
    )
    r = ctx.report
    r.run(s, "lists only the search tools", lambda: _same_tools(listed_tools(name), SEARCH_TOOLS))
    url = workload_url(name)
    r.run(
        s,
        "search finds find_pets_by_status",
        lambda: expect_result(
            url,
            "nw_search_tools",
            {"query": "find pets by status"},
            "pet_store_find_pets_by_status",
        ),
    )
    r.run(
        s,
        "nw_call_tool: unknown tool",
        lambda: expect_error(
            url,
            "nw_call_tool",
            {"name": "pet_store_no_such_tool", "arguments": {}},
            UNKNOWN_TOOL,
            seen=ctx.seen(s),
        ),
    )


def scenario_4(ctx: Context) -> None:
    s, name = SCENARIO_TITLES["4"], "slack-web-nw-mcp"
    run_host_workload(name, "slack-web-nw-mcp:latest", _host_env(ctx, "SLACK_WEB_ACCESS_TOKEN"))
    r = ctx.report
    r.run(
        s,
        "lists every slack_web tool",
        lambda: _same_tools(listed_tools(name), _host_tools("slack_web")),
    )
    url = workload_url(name)
    r.run(
        s,
        "post without channel and text",
        lambda: expect_error(
            url,
            "slack_web_chat_post_message",
            {},
            VALIDATION,
            seen=ctx.seen(s),
            mentions="'channel' is a required property",
        ),
    )
    if not ctx.has("SLACK_WEB_ACCESS_TOKEN"):
        r.run(
            s,
            "no token: auth_test fails",
            lambda: expect_error(url, "slack_web_auth_test", {}, MISSING_SECRET, seen=ctx.seen(s)),
        )


def scenario_5(ctx: Context) -> None:
    s, name = SCENARIO_TITLES["5"], "slack-web-nw-mcp-search"
    run_host_workload(name, "slack-web-nw-mcp:search", _host_env(ctx, "SLACK_WEB_ACCESS_TOKEN"))
    r = ctx.report
    r.run(s, "lists only the search tools", lambda: _same_tools(listed_tools(name), SEARCH_TOOLS))
    url = workload_url(name)
    r.run(
        s,
        "search finds chat_post_message",
        lambda: expect_result(
            url,
            "nw_search_tools",
            {"query": "post message to channel"},
            "slack_web_chat_post_message",
        ),
    )
    r.run(
        s,
        "nw_call_tool: post without channel and text",
        lambda: expect_error(
            url,
            "nw_call_tool",
            {"name": "slack_web_chat_post_message", "arguments": {}},
            VALIDATION,
            seen=ctx.seen(s),
            mentions="'channel' is a required property",
        ),
    )


def _stacklok_single(
    ctx: Context,
    s: str,
    *,
    name: str,
    port: int,
    token_var: str,
    scope: Path,
    invalid: tuple[str, Dict[str, Any]],
    no_token_call: tuple[str, Dict[str, Any]],
) -> None:
    token = ctx.tokens.get(token_var)
    url = run_stacklok_container(name, name, port, require_bearer=bool(token), env=ctx.telemetry())
    run_remote_workload(name, url, token=token)
    r = ctx.report
    r.run(
        s,
        "lists the scope's tools",  # config tools come only with multitenancy
        lambda: _same_tools(listed_tools(name), _scope_tools(scope)),
    )
    proxy = workload_url(name)
    r.run(
        s, "invalid arguments", lambda: expect_error(proxy, *invalid, VALIDATION, seen=ctx.seen(s))
    )
    r.run(
        s,
        "unknown tool",
        lambda: expect_error(proxy, "no_such_tool", {}, UNKNOWN_TOOL, seen=ctx.seen(s)),
    )
    if token:
        r.run(s, "direct request without a bearer: 401", lambda: expect_401(url))
    else:
        r.run(
            s,
            "no token: action fails",
            lambda: expect_error(proxy, *no_token_call, MISSING_UPSTREAM_TOKEN, seen=ctx.seen(s)),
        )


def scenario_3(ctx: Context) -> None:
    _stacklok_single(
        ctx,
        SCENARIO_TITLES["3"],
        name="petstore-mcp",
        port=18200,
        token_var="PET_STORE_API_KEY",
        scope=PETSTORE_SCOPE,
        invalid=("get_pet_by_id", {}),
        no_token_call=("find_pets_by_status", {"status": "available"}),
    )


def scenario_6(ctx: Context) -> None:
    _stacklok_single(
        ctx,
        SCENARIO_TITLES["6"],
        name="slack-post-mcp",
        port=18201,
        token_var="SLACK_WEB_ACCESS_TOKEN",
        scope=SLACK_SCOPE,
        invalid=("post_message", {}),
        no_token_call=("auth_test", {}),
    )


def _tenants(connector_id: str, block: Dict[str, Any]) -> Dict[str, Any]:
    """acme: live (default) + sandbox; globex: live."""

    def cfg(name: str, default: bool = False) -> Dict[str, Any]:
        return {**block, "name": name, "default": default}

    return {
        "tenants": {
            "acme": {connector_id: [cfg("live", default=True), cfg("sandbox")]},
            "globex": {connector_id: [cfg("live", default=True)]},
        }
    }


def _write_tenants(name: str, doc: Dict[str, Any]) -> Path:
    path = WORK_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    path.chmod(0o644)  # the container user is not the file owner
    return path


def scenario_7(ctx: Context) -> None:
    s, name = SCENARIO_TITLES["7"], "pet-store-nw-mcp-mt"
    # The host project's copy: the repo's wiring is restored after the build.
    host_config = REPO_ROOT / _host_project("pet_store") / "config" / "connectors.yaml"
    connectors = yaml.safe_load(host_config.read_text(encoding="utf-8"))
    block = {
        k: v
        for k, v in connectors["connectors"]["pet_store"].items()
        if k not in ("enabled", "exposed_via")
    }
    tenants = _write_tenants("tenants.host.yaml", _tenants("pet_store", block))
    env = {
        **ctx.telemetry(),
        "NW_MULTITENANCY_ENABLED": "true",
        "NW_TENANTS_PATH": "/app/config/tenants.yaml",
    }
    for tenant, config in (("ACME", "LIVE"), ("ACME", "SANDBOX"), ("GLOBEX", "LIVE")):
        for key in ("PET_STORE_API_KEY", "PET_STORE_ACCESS_TOKEN"):
            if ctx.has(key):
                env[f"NW_{tenant}_PET_STORE_{config}_{key}"] = ctx.tokens[key]
    run_host_workload(
        name,
        "pet-store-nw-mcp:latest",
        env,
        mounts=[f"{tenants}:/app/config/tenants.yaml:ro"],
    )
    r = ctx.report
    r.run(
        s,
        "lists pet_store tools + tenant tools",
        lambda: _same_tools(listed_tools(name), _host_tools("pet_store") | TENANT_TOOLS),
    )
    url = workload_url(name)
    # Tenant selection is per process: these run in order on one workload. (Without a selection a
    # call runs as __default__, which the image's connectors.yaml provides: docs/architecture/
    # tenancy.md.)
    r.run(
        s,
        "nw_list_tenants: acme, globex",
        lambda: expect_result(
            url, "nw_list_tenants", {"connector_id": "pet_store"}, "acme", "globex"
        ),
    )
    r.run(
        s,
        "select an unknown tenant",
        lambda: expect_error(
            url,
            "nw_select_tenant",
            {"tenant_id": "initech"},
            TENANT_NOT_ALLOWED,
            seen=ctx.seen(s),
            mentions="Unknown tenant 'initech'",
        ),
    )
    r.run(
        s,
        "select acme: configs live, sandbox",
        lambda: expect_result(url, "nw_select_tenant", {"tenant_id": "acme"}, "live", "sandbox"),
    )


def scenario_8(ctx: Context) -> None:
    s, container = SCENARIO_TITLES["8"], "petstore-mcp-mt"
    example = yaml.safe_load(
        (REPO_ROOT / "nw-stacklok-builder/out/petstore-mcp/config/tenants.example.yaml").read_text()
    )
    block = example["tenants"]["example-tenant"]["petstore3"][0]
    tenants = _write_tenants("tenants.stacklok.yaml", _tenants("petstore3", block))
    token = ctx.tokens.get("PET_STORE_API_KEY")
    url = run_stacklok_container(
        container,
        "petstore-mcp",
        18202,
        require_bearer=bool(token),
        tenants_file=tenants,
        env=ctx.telemetry(),
    )
    for tenant in ("acme", "globex", None):
        run_remote_workload(f"petstore-mcp-{tenant or 'notenant'}", url, token=token, tenant=tenant)
    r = ctx.report
    r.run(
        s,
        "acme lists the scope's tools + config tools",
        lambda: _same_tools(
            listed_tools("petstore-mcp-acme"), _scope_tools(PETSTORE_SCOPE) | CONFIG_TOOLS
        ),
    )
    acme, globex = workload_url("petstore-mcp-acme"), workload_url("petstore-mcp-globex")
    r.run(
        s,
        "acme configs: live, sandbox",
        lambda: expect_result(acme, "nw_list_configs", {}, "live", "sandbox"),
    )
    r.run(s, "globex configs: live only", lambda: _only_live(globex))
    r.run(
        s,
        "select an unknown config",
        lambda: expect_error(
            acme, "nw_select_config", {"config_name": "staging"}, CONFIG_NOT_FOUND, seen=ctx.seen(s)
        ),
    )
    r.run(
        s,
        "proxy without X-Tenant-ID",
        lambda: expect_error(
            workload_url("petstore-mcp-notenant"),
            "find_pets_by_status",
            {"status": "available"},
            MISSING_TENANT,
            seen=ctx.seen(s),
        ),
    )


def _only_live(url: str) -> Optional[str]:
    problem = expect_result(url, "nw_list_configs", {}, "live")
    if problem:
        return problem
    _, text = call_tool(url, "nw_list_configs", {})
    return "globex lists sandbox" if "sandbox" in text else None


SCENARIOS: Dict[str, Callable[[Context], None]] = {
    "1": scenario_1,
    "2": scenario_2,
    "3": scenario_3,
    "4": scenario_4,
    "5": scenario_5,
    "6": scenario_6,
    "7": scenario_7,
    "8": scenario_8,
}
# The build job each scenario's image comes from.
SCENARIO_IMAGES = {
    "1": "pet_store:image-latest",
    "2": "pet_store:image-search",
    "3": "petstore3:image",
    "4": "slack_web:image-latest",
    "5": "slack_web:image-search",
    "6": "slack_web:stacklok-image",
    "7": "pet_store:image-latest",
    "8": "petstore3:image",
}
WORKLOADS = [
    "pet-store-nw-mcp",
    "pet-store-nw-mcp-search",
    "petstore-mcp",
    "slack-web-nw-mcp",
    "slack-web-nw-mcp-search",
    "slack-post-mcp",
    "pet-store-nw-mcp-mt",
    "petstore-mcp-acme",
    "petstore-mcp-globex",
    "petstore-mcp-notenant",
]
CONTAINERS = ["petstore-mcp", "slack-post-mcp", "petstore-mcp-mt"]


# The OpenTelemetry service.name each scenario's server reports under.
SCENARIO_SERVICES = {
    "1": "nw-pet_store",
    "2": "nw-pet_store",
    "7": "nw-pet_store",
    "4": "nw-slack_web",
    "5": "nw-slack_web",
    "3": "petstore-mcp",
    "8": "petstore-mcp",
    "6": "slack-post-mcp",
}


def _loki(grafana: str, query: str, *, since: float) -> List[Dict[str, Any]]:
    """Log streams matching ``query``, through Grafana's Loki datasource (uid ``loki``)."""
    reply = httpx.get(
        f"{grafana}/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
        params={"query": query, "start": str(int(since * 1e9)), "limit": "5"},
        timeout=15,
    )
    reply.raise_for_status()
    return list(reply.json()["data"]["result"])


def _logged(
    grafana: str, service: str, errors: List[tuple[str, str]], since: float
) -> Optional[str]:
    """Each (code, trace_id) is a log line of ``service``, with that error_code; else any line."""
    deadline = time.monotonic() + 60  # the log exporter batches every few seconds
    while True:
        missing = [
            f"{code} trace_id={trace}"
            for code, trace in errors
            if not _loki(
                grafana,
                f'{{service_name="{service}"}} | trace_id="{trace}" | error_code="{code}"',
                since=since,
            )
        ]
        if not errors and not _loki(grafana, f'{{service_name="{service}"}}', since=since):
            missing = [f"any log line of {service}"]
        if not missing:
            return None
        if time.monotonic() > deadline:
            return f"not in Grafana's Loki: {', '.join(missing[:4])}"
        time.sleep(3)


def check_telemetry(ctx: Context, scenarios: List[str], *, grafana: str, since: float) -> None:
    for key in scenarios:
        title, service = SCENARIO_TITLES[key], SCENARIO_SERVICES[key]
        errors = list(ctx.seen(title))
        what = (
            f"its {len(errors)} failed calls are in Grafana by trace_id"
            if errors
            else "logs reach Grafana"
        )
        ctx.report.run(
            title, f"telemetry: {what}", lambda: _logged(grafana, service, errors, since)
        )
        if any(code in _CONNECTOR_RUN_CODES for code, _ in errors):
            ctx.report.run(
                title,
                "telemetry: connector runs reach Tempo",
                lambda: _traced(grafana, service, since),
            )


# Failures raised inside the connector run, so they come with a connector.run span.
_CONNECTOR_RUN_CODES = {MISSING_SECRET[0], MISSING_UPSTREAM_TOKEN[0]}


def _traced(grafana: str, service: str, since: float) -> Optional[str]:
    """``service`` has exported at least one span to Tempo since ``since``."""
    deadline = time.monotonic() + 60  # the span exporter batches every few seconds
    while True:
        reply = httpx.get(
            f"{grafana}/api/datasources/proxy/uid/tempo/api/search",
            params={
                "q": f'{{resource.service.name="{service}"}}',
                "start": str(int(since)),
                "end": str(int(time.time()) + 60),
                "limit": "5",
            },
            timeout=15,
        )
        reply.raise_for_status()
        if reply.json().get("traces"):
            return None
        if time.monotonic() > deadline:
            return f"no {service} spans in Grafana's Tempo"
        time.sleep(3)


def deploy_and_check(scenarios: List[str], ctx: Context, *, workers: int) -> None:
    def one(key: str) -> None:
        try:
            SCENARIOS[key](ctx)
        except Exception as exc:  # noqa: BLE001 — a scenario that cannot deploy fails, others go on
            ctx.report.add(SCENARIO_TITLES[key], "deploy", False, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, scenarios))


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------


def preflight(*, build: bool) -> List[str]:
    problems = []
    if _cmd("docker", "info", check=False).returncode != 0:
        problems.append("Docker is not running")
    if _cmd("thv", "version", check=False).returncode != 0:
        problems.append("`thv` (ToolHive) is not on PATH")
    if build:
        dockerfile = (REPO_ROOT / "nw-stacklok-builder/template/Dockerfile").read_text()
        # The tag alone: `docker manifest inspect` fails its own check on tag@digest references.
        base = re.search(r"^FROM (dhi\.io/[^@\s]+)", dockerfile, flags=re.M)
        if base and _cmd("docker", "manifest", "inspect", base.group(1), check=False).returncode:
            problems.append("cannot pull stacklok's base image: run `docker login dhi.io`")
    return problems


def clean() -> None:
    for name in WORKLOADS:
        _remove_workload(name)
    for container in CONTAINERS:
        _cmd("docker", "rm", "-f", container, check=False)
    for path in (WORK_DIR / "secrets").glob("*"):
        path.unlink()
    print("Removed the ToolHive workloads, stacklok containers and token files.")


def print_report(report: Report, build: Dict[str, str]) -> bool:
    ok = all(v == "ok" for v in build.values()) and all(r.ok for r in report.results)
    width = max((len(r.scenario) for r in report.results), default=10)
    print()
    for result in sorted(report.results, key=lambda r: r.scenario):
        mark = "PASS" if result.ok else "FAIL"
        line = f"  {mark}  {result.scenario:<{width}}  {result.check}"
        print(line + (f"\n          {result.detail}" if result.detail else ""))
    passed = sum(r.ok for r in report.results)
    print(f"\n{passed}/{len(report.results)} checks passed.")
    return ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-build", action="store_true", help="use the images already built")
    parser.add_argument("--clean", action="store_true", help="remove what this script started")
    parser.add_argument("--jobs", type=int, default=3, help="parallel build jobs (default 3)")
    parser.add_argument(
        "--scenario", action="append", choices=sorted(SCENARIOS), help="only these (repeatable)"
    )
    parser.add_argument(
        "--otlp-endpoint",
        metavar="URL",
        help="OTLP/HTTP collector for the servers' logs and traces, as the containers reach it "
        "(e.g. http://host.docker.internal:4318, the grafana/ stack); then checks they arrive",
    )
    parser.add_argument(
        "--grafana",
        metavar="URL",
        default="http://localhost:3000",
        help="Grafana in front of that collector, for the telemetry check (default %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.clean:
        clean()
        return 0

    problems = preflight(build=not args.skip_build)
    if args.otlp_endpoint:
        try:
            httpx.get(f"{args.grafana.rstrip('/')}/api/health", timeout=5).raise_for_status()
        except httpx.HTTPError as exc:
            problems.append(f"Grafana at {args.grafana} is not up ({exc}): see grafana/README.md")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    tokens = {k: os.environ[k] for k in TOKEN_VARS if os.environ.get(k)}
    print("Tokens: " + (", ".join(tokens) or "none (actions are expected to fail)"))
    run_dir = WORK_DIR / "logs" / time.strftime("%Y%m%d-%H%M%S")
    scenarios = args.scenario or sorted(SCENARIOS)

    build: Dict[str, str] = {}
    if not args.skip_build:
        print(f"\nBuilding (logs in {run_dir.relative_to(REPO_ROOT)}):")
        with restored(WIRING_FILES):
            build = run_jobs(build_jobs(), workers=args.jobs, log_dir=run_dir)
    ready = [s for s in scenarios if build.get(SCENARIO_IMAGES[s], "ok") == "ok"]
    report = Report()
    for s in sorted(set(scenarios) - set(ready)):
        report.add(
            SCENARIO_TITLES[s], "build", False, f"{SCENARIO_IMAGES[s]} did not build; see its log"
        )

    print(f"\nDeploying and checking scenarios {', '.join(ready) or 'none'}…")
    if args.otlp_endpoint:
        print(f"Telemetry: {args.otlp_endpoint} (checked through {args.grafana})")
    ctx = Context(tokens, report, args.otlp_endpoint)
    deployed = time.time()
    deploy_and_check(ready, ctx, workers=args.jobs)
    if args.otlp_endpoint:
        check_telemetry(ctx, ready, grafana=args.grafana.rstrip("/"), since=deployed)
    return 0 if print_report(report, build) else 1


if __name__ == "__main__":
    sys.exit(main())

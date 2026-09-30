# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Stages of ``nw gen-stacklok``: scope → connector → image wheels → stacklok server."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import yaml

from nw_cli.prerequisites import require_docker
from nw_cli.stages import LogFn, StageError, run_logged_command
from nw_cli.wheel_cache import is_fresh, record_build

if TYPE_CHECKING:
    from nw_stacklok.wheels import WheelTarget

STACKLOK_BUILDER_DIR = "nw-stacklok-builder"
# Set on specs prepared by `nw gen-stacklok --path`: the original spec the file was made from.
# The connector is built from that original; stacklok's tools read the prepared copy.
PREPARED_SOURCE_KEY = "x-nw-source"


def stacklok_packages(connector_id: str) -> list[str]:
    """Packages whose wheels a stacklok-built server installs."""
    return [
        "packages/runtime",
        "packages/bindings",
        "packages/toolhive",
        f"packages/connectors/{connector_id}",
    ]


def default_output_dir(node_wire_root: Path) -> Path:
    return node_wire_root / STACKLOK_BUILDER_DIR / "out"


def template_dir(node_wire_root: Path) -> Path:
    return node_wire_root / STACKLOK_BUILDER_DIR / "template"


def prepared_spec_path(node_wire_root: Path, name: str) -> Path:
    return node_wire_root / STACKLOK_BUILDER_DIR / "specs" / f"{name}.openapi.json"


@dataclass(frozen=True)
class StacklokScope:
    """The user's ``mcp-scope.yaml`` with the node-wire runtime block settled."""

    path: Path
    connector_id: str
    spec_source: str  # what stacklok reads
    connector_spec_source: str  # what nw-connector-builder builds the connector from
    base_url: str | None
    server_name: str
    document: Dict[str, Any]

    def write(self, work_dir: Path) -> Path:
        """Write the effective scope (with ``runtime: node_wire``) into ``work_dir``."""
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / "mcp-scope.yaml"
        target.write_text(yaml.safe_dump(self.document, sort_keys=False), encoding="utf-8")
        return target


# RFC 1123 label, the same rule as stacklok's ServerConfig.validate_dns_label.
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def read_scope(scope_path: Path, connector_id: str | None) -> StacklokScope:
    """Load a stacklok scope and bind it to ``connector_id``.

    The id comes from ``--connector-id`` or the scope's own ``runtime:`` block; when both are
    given they must agree.
    """
    try:
        document = yaml.safe_load(scope_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise StageError(f"Cannot read scope {scope_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise StageError(f"Scope {scope_path} is not a YAML mapping")
    runtime = document.get("runtime") or {}
    scoped_id = runtime.get("connector_id") if isinstance(runtime, dict) else None
    if connector_id and scoped_id and connector_id != scoped_id:
        raise StageError(
            f"--connector-id {connector_id!r} does not match the scope's runtime.connector_id "
            f"{scoped_id!r}"
        )
    cid = connector_id or scoped_id
    if not cid:
        raise StageError("Pass --connector-id (the scope has no runtime.connector_id)")
    document["runtime"] = {"type": "node_wire", "connector_id": cid}
    spec = document.get("spec") or {}
    source = str(spec.get("source") or "").strip()
    if not source:
        raise StageError(f"Scope {scope_path} has no spec.source")
    server = str((document.get("server") or {}).get("name") or "").strip()
    if not server:
        raise StageError(f"Scope {scope_path} has no server.name")
    # Checked here, not only by stacklok's validator later: the name becomes the output
    # directory (replaced under --force), image name and Kubernetes names.
    if not _DNS_LABEL.fullmatch(server):
        raise StageError(
            f"Scope {scope_path} has an invalid server.name {server!r}: use lowercase letters, "
            "digits and hyphens, starting and ending with a letter or digit (max 63)"
        )
    spec_source = _resolve_source(source, scope_path.parent)
    return StacklokScope(
        path=scope_path,
        connector_id=cid,
        spec_source=spec_source,
        connector_spec_source=_prepared_origin(spec_source) or spec_source,
        base_url=(str(spec["base_url"]).strip() or None) if spec.get("base_url") else None,
        server_name=server,
        document=document,
    )


def _resolve_source(source: str, scope_dir: Path) -> str:
    """URLs as-is; relative paths against the scope's folder, else the working directory
    (the ai-scoping skill writes the path it was given, usually relative to where it ran)."""
    if source.startswith(("http://", "https://")):
        return source
    path = Path(source)
    if path.is_absolute():
        return str(path)
    for base in (scope_dir, Path.cwd()):
        if (base / path).is_file():
            return str((base / path).resolve())
    return str((scope_dir / path).resolve())


def _prepared_origin(spec_source: str) -> str | None:
    """The original spec behind a file written by :func:`prepare_spec`, if it is one."""
    path = Path(spec_source)
    if spec_source.startswith(("http://", "https://")) or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    origin = doc.get(PREPARED_SOURCE_KEY) if isinstance(doc, dict) else None
    return str(origin) if origin else None


def _load_strict(spec_source: str) -> tuple[Dict[str, Any], str, bool, bool]:
    """Load ``spec_source`` as a strict OpenAPI 3.0 document for stacklok's parser.

    Converts Swagger 2.0 and applies :func:`strict_openapi30` to 3.0.x documents (3.1 allows
    those JSON-Schema forms). Returns ``(doc, origin, from_url, changed)``.
    """
    from nw_connector_builder.load import detect_version, load_raw_document
    from nw_connector_builder.normalize_v2 import normalize_swagger2_to_openapi3

    doc, origin, from_url = load_raw_document(spec_source)
    changed = detect_version(doc) == "2.0"
    if changed:
        doc = normalize_swagger2_to_openapi3(doc)
    if str(doc.get("openapi", "")).startswith("3.0"):
        changed = strict_openapi30(doc) or changed
    return doc, origin, from_url, changed


def prepare_spec(spec_source: str, target: Path) -> Path:
    """Write the local OpenAPI 3.0 file stacklok's Phase 1 (ai-scoping) and Phase 3 read.

    Always written (see :func:`_load_strict`); records the original under ``x-nw-source`` so the
    connector is still built from it.
    """
    doc, origin, from_url, _changed = _load_strict(spec_source)
    doc[PREPARED_SOURCE_KEY] = origin if from_url else str(Path(origin).resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return target


def materialize_spec(spec_source: str, work_dir: Path) -> Path:
    """A local, strict OpenAPI 3.0 file for stacklok's loader (see :func:`_load_strict`).

    An unchanged local file is used as-is, and so is one :func:`prepare_spec` wrote.
    """
    if _prepared_origin(spec_source) is not None:
        return Path(spec_source)
    doc, _origin, from_url, changed = _load_strict(spec_source)
    if not from_url and not changed:
        return Path(spec_source)
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "openapi.json"
    target.write_text(json.dumps(doc, default=str), encoding="utf-8")
    return target


def strict_openapi30(node: Any) -> bool:
    """Rewrite, in place, JSON-Schema forms that OpenAPI 3.0 does not allow; True if any.

    Some specs (Slack's among them) use ``type: [string, "null"]``, ``type: "null"`` and tuple
    ``items: [...]``. nw-connector-builder tolerates them; stacklok's strict parser does not.
    They become ``nullable: true``, ``anyOf`` and a single ``items`` schema.
    """
    changed = False
    if isinstance(node, list):
        for item in node:
            changed = strict_openapi30(item) or changed
        return changed
    if not isinstance(node, dict):
        return False
    kind = node.get("type")
    if isinstance(kind, list):
        types = [k for k in kind if k != "null"]
        if len(types) < len(kind):
            node["nullable"] = True
        if len(types) == 1:
            node["type"] = types[0]
        else:
            del node["type"]
            if types:
                node["anyOf"] = [{"type": k} for k in types]
        changed = True
    elif kind == "null":
        del node["type"]
        node["nullable"] = True
        changed = True
    items = node.get("items")
    if isinstance(items, list):
        node["items"] = items[0] if len(items) == 1 else ({"anyOf": items} if items else {})
        changed = True
    for value in node.values():
        changed = strict_openapi30(value) or changed
    return changed


_WHEEL_ARCH_ALIASES = {
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
}


def image_wheel_target(node_wire_root: Path) -> WheelTarget:
    """The wheel flavour the generated server's image needs (from the template's base image)."""
    from nw_stacklok.wheels import WheelTarget

    return WheelTarget.from_dockerfile(template_dir(node_wire_root) / "Dockerfile")


def wheel_arches() -> list[str]:
    """``NW_WHEEL_ARCHS`` (e.g. ``"x86_64 aarch64"``), else the host's architecture."""
    raw = os.environ.get("NW_WHEEL_ARCHS", "").split()
    machine = platform.machine().lower()
    return sorted(raw) if raw else [_WHEEL_ARCH_ALIASES.get(machine, machine)]


def _stamp_key(target: WheelTarget) -> str:
    return f"{target.python}-{target.libc}"


def run_stacklok_wheel_build(
    node_wire_root: Path,
    connector_id: str,
    *,
    log: LogFn | None = None,
    output: LogFn | None = None,
) -> list[str]:
    """Wheels for the generated server's image; returns the packages it had to (re)build.

    The flavour (Python ABI + libc) comes from the image, e.g. cp313 musllinux for stacklok's
    Alpine base. A package whose sources are unchanged since its last build, and whose wheels
    for every requested architecture are present, is reused rather than recompiled.
    ``log`` gets these decisions; ``output`` (default ``log``) the build tool's output.
    """
    say = log or print
    target = image_wheel_target(node_wire_root)
    arches = wheel_arches()
    say(f"Target: {target.description}, arch {' '.join(arches)}")
    packages = stacklok_packages(connector_id)
    stale: list[str] = []
    for package in packages:
        present = set(target.wheels_by_arch(node_wire_root / package / "dist"))
        if not (is_fresh(node_wire_root, package, _stamp_key(target)) and set(arches) <= present):
            stale.append(package)
    reused = [p for p in packages if p not in stale]
    if reused:
        say("Up to date, reused: " + ", ".join(reused))
    if not stale:
        return []
    require_docker("the image wheel build (cibuildwheel)")
    say("Building: " + ", ".join(stale))
    cmd = ["bash", "scripts/build-packages.sh", "--cibw-linux", *stale]
    env = {**os.environ, "CIBW_BUILD": target.cibw_build, "NW_WHEEL_ARCHS": " ".join(arches)}
    started = time.time()
    code = run_logged_command(cmd, cwd=node_wire_root, log=output or log, env=env)
    if code != 0:
        raise StageError(f"Wheel build failed (exit {code}): {' '.join(cmd)}")
    for package in stale:
        record_build(node_wire_root, package, _stamp_key(target), since=started)
    return stale


@dataclass(frozen=True)
class ScopeCheck:
    """stacklok's validator verdict on a scope (against its spec)."""

    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_stacklok_scope(scope: StacklokScope, work_dir: Path) -> tuple[ScopeCheck, Path, Path]:
    """Run stacklok's validator; returns the verdict and the effective scope and spec files.

    Fast (no build), so callers run it before the slow stages. A scope stacklok cannot even
    load is reported as an error, not raised.
    """
    from mcp_builder.log import configure_logging
    from mcp_builder.schema.models import load_scope
    from mcp_builder.spec import load_openapi_spec
    from mcp_builder.validate import validate_scope

    # stacklok logs every step at info/debug; the verdict is reported by the caller.
    configure_logging(level="warning")
    scope_file = scope.write(work_dir)
    spec_file = materialize_spec(scope.spec_source, work_dir)
    try:
        result = validate_scope(load_scope(scope_file), load_openapi_spec(spec_file))
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return (
            ScopeCheck(errors=[f"stacklok cannot load the scope: {exc}"], warnings=[]),
            scope_file,
            spec_file,
        )
    return (
        ScopeCheck(errors=list(result.errors), warnings=list(result.warnings)),
        scope_file,
        spec_file,
    )


def run_stacklok_generate(
    node_wire_root: Path,
    scope: StacklokScope,
    *,
    output_dir: Path,
    work_dir: Path,
    force_output: bool = False,
    wheels: bool = True,
    lock: bool = True,
    log: LogFn | None = None,
) -> Path:
    """Validate the scope with stacklok's validator, then run its generator in node-wire mode."""
    from mcp_builder.log import configure_logging
    from mcp_builder.pipeline import run_pipeline
    from nw_stacklok.hooks import NodeWireOptions

    # stacklok logs every planning step at debug; keep the progress display readable.
    configure_logging(level="warning")

    check, scope_file, spec_file = validate_stacklok_scope(scope, work_dir)
    for warning in check.warnings:
        (log or print)(f"scope warning: {warning}")
    if check.errors:
        raise StageError("Scope validation failed:\n" + "\n".join(f"  - {e}" for e in check.errors))

    project = output_dir / f"{scope.server_name}-mcp"
    if project.exists():
        if not force_output:
            raise StageError(f"Output project already exists: {project} (pass --force)")
        if output_dir.resolve() not in project.resolve().parents:
            raise StageError(f"Refusing to replace {project}: it is outside {output_dir}")
        shutil.rmtree(project)
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_pipeline(
        scope_file,
        spec_file,
        template_dir(node_wire_root),
        output_dir,
        node_wire=NodeWireOptions(node_wire_root, wheels=wheels, lock=lock),
    )

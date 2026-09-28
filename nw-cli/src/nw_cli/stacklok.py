# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Stages of ``nw gen-stacklok``: scope → connector → musllinux wheels → stacklok server."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from nw_cli.stages import LogFn, StageError, run_logged_command

STACKLOK_BUILDER_DIR = "nw-stacklok-builder"


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


@dataclass(frozen=True)
class StacklokScope:
    """The user's ``mcp-scope.yaml`` with the node-wire runtime block settled."""

    path: Path
    connector_id: str
    spec_source: str
    base_url: str | None
    server_name: str
    document: Dict[str, Any]

    def write(self, work_dir: Path) -> Path:
        """Write the effective scope (with ``runtime: node_wire``) into ``work_dir``."""
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / "mcp-scope.yaml"
        target.write_text(yaml.safe_dump(self.document, sort_keys=False), encoding="utf-8")
        return target


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
    return StacklokScope(
        path=scope_path,
        connector_id=cid,
        spec_source=_resolve_source(source, scope_path.parent),
        base_url=(str(spec["base_url"]).strip() or None) if spec.get("base_url") else None,
        server_name=server,
        document=document,
    )


def _resolve_source(source: str, scope_dir: Path) -> str:
    if source.startswith(("http://", "https://")):
        return source
    path = Path(source)
    return str(path if path.is_absolute() else (scope_dir / path).resolve())


def materialize_spec(spec_source: str, work_dir: Path) -> Path:
    """A local, strict OpenAPI 3.0 file for stacklok's loader.

    Downloads URLs, converts Swagger 2.0, and rewrites JSON-Schema forms OpenAPI 3.0 rejects
    (see :func:`strict_openapi30`). An unchanged local file is used as-is.
    """
    from nw_connector_builder.load import detect_version, load_raw_document
    from nw_connector_builder.normalize_v2 import normalize_swagger2_to_openapi3

    doc, _origin, from_url = load_raw_document(spec_source)
    version = detect_version(doc)
    if version == "2.0":
        doc = normalize_swagger2_to_openapi3(doc)
    changed = strict_openapi30(doc) if str(doc.get("openapi", "")).startswith("3.0") else False
    if not from_url and version != "2.0" and not changed:
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


def run_stacklok_wheel_build(
    node_wire_root: Path, connector_id: str, *, log: LogFn | None = None
) -> None:
    """cp313 musllinux wheels (stacklok's Alpine image) for runtime, bindings, toolhive, connector."""
    cmd = ["bash", "scripts/build-packages.sh", "--musllinux", *stacklok_packages(connector_id)]
    code = run_logged_command(cmd, cwd=node_wire_root, log=log)
    if code != 0:
        raise StageError(f"Wheel build failed (exit {code}): {' '.join(cmd)}")


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
    from mcp_builder.schema.models import load_scope
    from mcp_builder.spec import load_openapi_spec
    from mcp_builder.validate import validate_scope
    from nw_stacklok.hooks import NodeWireOptions

    # stacklok logs every planning step at debug; keep the progress display readable.
    configure_logging(level="warning")

    scope_file = scope.write(work_dir)
    spec_file = materialize_spec(scope.spec_source, work_dir)
    result = validate_scope(load_scope(scope_file), load_openapi_spec(spec_file))
    for warning in result.warnings:
        (log or print)(f"scope warning: {warning}")
    if result.errors:
        raise StageError(
            "Scope validation failed:\n" + "\n".join(f"  - {e}" for e in result.errors)
        )

    project = output_dir / f"{scope.server_name}-mcp"
    if project.exists():
        if not force_output:
            raise StageError(f"Output project already exists: {project} (pass --force)")
        shutil.rmtree(project)
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_pipeline(
        scope_file,
        spec_file,
        template_dir(node_wire_root),
        output_dir,
        node_wire=NodeWireOptions(node_wire_root, wheels=wheels, lock=lock),
    )

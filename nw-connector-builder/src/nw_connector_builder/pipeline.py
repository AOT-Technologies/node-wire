# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Orchestration: load → derive → stage → gate → promote → mcp → wire."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from nw_connector_builder.codegen import write_staging
from nw_connector_builder.derive import derive_operations
from nw_connector_builder.derive.operations import DeriveError
from nw_connector_builder.gate import run_gate
from nw_connector_builder.load import SpecLoadError, load_openapi_document
from nw_connector_builder.mcp_handoff import run_mcp_handoff
from nw_connector_builder.promote import PromoteError, promote
from nw_connector_builder.report import build_report, print_report, write_report
from nw_connector_builder.wire import WireError, apply_wire

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """Hard build failure (exit 1)."""


class UsageError(Exception):
    """CLI usage error (exit 2)."""


def run_build(
    *,
    spec: str,
    connector_id: str,
    node_wire_root: Path,
    wire: bool = False,
    force: bool = False,
    no_mcp: bool = False,
    base_url: str | None = None,
    report_path: Path | None = None,
) -> int:
    """Return process exit code (0 success, 1 hard/post-promote failure)."""
    abort_report_path = report_path or (Path.cwd() / "report.json")
    result = None
    meta: dict = {"origin": spec}

    try:
        doc, meta = load_openapi_document(spec, base_url_override=base_url)
        result = derive_operations(
            doc, connector_id=connector_id, base_url_override=base_url
        )
    except SpecLoadError as exc:
        report = build_report(
            connector_id=connector_id, meta=meta, result=None, error=str(exc)
        )
        print_report(report)
        write_report(report, abort_report_path)
        raise BuildError(str(exc)) from exc
    except DeriveError as exc:
        report = build_report(
            connector_id=connector_id, meta=meta, result=None, error=str(exc)
        )
        print_report(report)
        write_report(report, abort_report_path)
        raise BuildError(str(exc)) from exc

    staging_root = Path(tempfile.mkdtemp(prefix=f"nw-cb-{connector_id}-"))
    exit_code = 0
    try:
        preliminary = build_report(connector_id=connector_id, meta=meta, result=result)
        write_staging(staging_root, connector_id, result, preliminary)

        gate = run_gate(staging_root, connector_id)
        gate_info = {
            "ok": gate.ok,
            "import_ok": gate.import_ok,
            "pytest_ok": gate.pytest_ok,
            "message": gate.message,
            "pytest_output": gate.pytest_output[-4000:] if gate.pytest_output else "",
        }
        if not gate.ok:
            report = build_report(
                connector_id=connector_id,
                meta=meta,
                result=result,
                gate=gate_info,
                error=gate.message,
            )
            print_report(report)
            write_report(report, abort_report_path)
            raise BuildError(gate.message)

        try:
            promote(staging_root, node_wire_root, connector_id, force=force)
        except PromoteError as exc:
            raise UsageError(str(exc)) from exc

        # Canonical report beside package
        pkg_report = (
            node_wire_root / "packages" / "connectors" / connector_id / "report.json"
        )

        mcp_info = None
        if not no_mcp:
            mcp_info = run_mcp_handoff(
                connector_id,
                node_wire_root=node_wire_root,
                force_output=force,
            )
            if not mcp_info.get("ok"):
                exit_code = 1

        wire_info = None
        if wire:
            try:
                apply_wire(
                    node_wire_root,
                    connector_id,
                    base_url=result.default_base_url,
                    auth_block=result.auth_plan.yaml_block,
                    secret_key=result.auth_plan.secret_key,
                )
                wire_info = {"ok": True, "wired": True}
            except WireError as exc:
                wire_info = {"ok": False, "error": str(exc)}
                exit_code = 1

        report = build_report(
            connector_id=connector_id,
            meta=meta,
            result=result,
            gate=gate_info,
            mcp=mcp_info,
            wire=wire_info,
        )
        print_report(report)
        write_report(report, pkg_report)
        return exit_code
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

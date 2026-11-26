# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Orchestration: load → derive → stage → gate → promote → mcp → wire."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nw_connector_builder.codegen import CodegenFormatError, ruff_config_for, write_staging
from nw_connector_builder.derive import derive_operations
from nw_connector_builder.derive.operations import DeriveError
from nw_connector_builder.gate import run_gate
from nw_connector_builder.load import SpecLoadError, load_openapi_document
from nw_connector_builder.mcp_handoff import run_mcp_handoff
from nw_connector_builder.options import apply_auth_override, parse_headers
from nw_connector_builder.promote import (
    PromoteError,
    assert_generator_owned,
    promote,
    recover_interrupted_promote,
)
from nw_connector_builder.report import build_report, print_report, write_report
from nw_connector_builder.wire import WireError, wire_connector

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """Hard build failure before anything was promoted (exit 1).

    ``report_path``: where the abort report was written.
    """

    def __init__(self, message: str, *, report_path: Path | None = None) -> None:
        super().__init__(message)
        self.report_path = report_path


@dataclass(frozen=True)
class BuildOutcome:
    """What a build that reached promote did. Earlier failures raise instead.

    ``stage_reached``: ``promoted``, ``wired`` or ``mcp`` — the furthest stage, in that order,
    reached with nothing failed before it.
    ``mcp`` / ``wire``: the hand-off and wiring results (None when not requested).
    """

    stage_reached: str
    promoted: bool
    mcp: dict[str, Any] | None
    wire: dict[str, Any] | None
    report: dict[str, Any]
    report_path: Path

    @property
    def ok(self) -> bool:
        return all(step is None or step.get("ok") for step in (self.mcp, self.wire))

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


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
    auth: str | None = None,
    headers: list[str] | dict[str, str] | None = None,
) -> BuildOutcome:
    """Build, gate and promote a connector, then optionally hand off to MCP and wire it in.

    Failures before promote raise (:class:`UsageError`, :class:`BuildError`; the repo is left
    untouched and the abort report goes to ``report_path``, default ``./report.json``). After
    promote the result is a :class:`BuildOutcome`, whose ``exit_code`` is the process exit code;
    its report is ``packages/connectors/<id>/report.json``.

    ``auth``: ``--auth`` (``bearer`` / ``header:<name>`` / ``query:<name>``) for a spec that
    declares no auth. ``headers``: ``--header NAME=VALUE`` strings (or a parsed mapping) sent
    on every request.
    """
    abort_report_path = report_path or (Path.cwd() / "report.json")
    try:
        static_headers = headers if isinstance(headers, dict) else parse_headers(headers)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    # Before deriving, staging, or importing anything: the gate imports the
    # generated module in-process, so a colliding id would mutate global runtime
    # state (its declare_secret_shape call) even on a build that never promotes.
    try:
        recover_interrupted_promote(node_wire_root, connector_id)
        assert_generator_owned(node_wire_root, connector_id)
    except PromoteError as exc:
        raise UsageError(str(exc)) from exc
    result = None
    meta: dict = {"origin": spec}

    try:
        doc, meta = load_openapi_document(spec, base_url_override=base_url)
        auth_note = None
        if auth:
            try:
                auth_note = apply_auth_override(doc, auth)
            except ValueError as exc:
                raise UsageError(str(exc)) from exc
        result = derive_operations(doc, connector_id=connector_id, base_url_override=base_url)
        if auth_note:
            result.notes.append(auth_note)
        result.static_headers = dict(static_headers)
    except SpecLoadError as exc:
        report = build_report(
            connector_id=connector_id,
            meta=exc.meta or meta,
            result=None,
            error=str(exc),
        )
        print_report(report)
        write_report(report, abort_report_path)
        raise BuildError(str(exc), report_path=abort_report_path) from exc
    except DeriveError as exc:
        report = build_report(connector_id=connector_id, meta=meta, result=None, error=str(exc))
        print_report(report)
        write_report(report, abort_report_path)
        raise BuildError(str(exc), report_path=abort_report_path) from exc

    staging_root = Path(tempfile.mkdtemp(prefix=f"nw-cb-{connector_id}-"))
    try:
        preliminary = build_report(connector_id=connector_id, meta=meta, result=result)
        try:
            write_staging(
                staging_root,
                connector_id,
                result,
                preliminary,
                ruff_config=ruff_config_for(node_wire_root),
            )
        except CodegenFormatError as exc:
            report = build_report(
                connector_id=connector_id, meta=meta, result=result, error=str(exc)
            )
            print_report(report)
            write_report(report, abort_report_path)
            raise BuildError(str(exc), report_path=abort_report_path) from exc

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
            raise BuildError(gate.message, report_path=abort_report_path)

        try:
            promote(staging_root, node_wire_root, connector_id, force=force)
        except PromoteError as exc:
            raise UsageError(str(exc)) from exc
        stage_reached = "promoted"

        # Canonical report beside package
        pkg_report = node_wire_root / "packages" / "connectors" / connector_id / "report.json"

        # Wired before the MCP hand-off: the host bakes the connector's connectors.yaml entry.
        wire_info = None
        if wire:
            try:
                wire_info = wire_connector(
                    node_wire_root,
                    connector_id,
                    base_url=result.default_base_url,
                    auth_block=result.auth_plan.yaml_block,
                    secret_keys=result.auth_plan.secret_keys,
                    secret_defaults=result.auth_plan.secret_defaults,
                    host_supplied=result.auth_plan.tier == "host_supplied",
                    extra_auth_plans=result.extra_auth_plans,
                    headers=result.static_headers,
                )
            except WireError as exc:
                wire_info = {"ok": False, "error": str(exc)}

        mcp_info = None
        if not no_mcp:
            mcp_info = run_mcp_handoff(
                connector_id,
                node_wire_root=node_wire_root,
                force_output=force,
            )

        # The furthest stage reached with nothing failed before it: promoted -> wired -> mcp.
        if wire_info is None or wire_info.get("ok"):
            if wire_info is not None:
                stage_reached = "wired"
            if mcp_info is not None and mcp_info.get("ok"):
                stage_reached = "mcp"

        from nw_mcp_builder.wheel_cache import package_source_hash

        report = build_report(
            connector_id=connector_id,
            meta=meta,
            result=result,
            gate=gate_info,
            mcp=mcp_info,
            wire=wire_info,
            source_hash=package_source_hash(node_wire_root, f"packages/connectors/{connector_id}"),
        )
        print_report(report)
        write_report(report, pkg_report)
        return BuildOutcome(
            stage_reached=stage_reached,
            promoted=True,
            mcp=mcp_info,
            wire=wire_info,
            report=report,
            report_path=pkg_report,
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

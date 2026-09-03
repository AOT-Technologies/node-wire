# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Build report (stdout + report.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nw_connector_builder import __version__
from nw_connector_builder.derive.operations import DeriveResult


def build_report(
    *,
    connector_id: str,
    meta: dict[str, Any],
    result: DeriveResult | None,
    gate: dict[str, Any] | None = None,
    mcp: dict[str, Any] | None = None,
    wire: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": connector_id,
        "spec_source": meta.get("origin"),
        "spec_version": meta.get("spec_version"),
        "generator_version": __version__,
        "spec_content_hash": meta.get("content_hash"),
        "error": error,
    }
    report: dict[str, Any] = {"summary": summary}

    if result is not None:
        summary.update(
            {
                "total_operations": result.total_operations,
                "generated": len(result.actions),
                "soft_dropped": len(result.drops),
                "deprecated": sum(1 for a in result.actions if a.deprecated),
                "coverage_warning": result.coverage_warning,
                "default_base_url": result.default_base_url,
            }
        )
        report["generated_actions"] = [
            {"name": a.name, "method": a.method, "path": a.path, "auth": a.auth}
            for a in result.actions
        ]
        report["skipped"] = [
            {
                "method": d.method,
                "path": d.path,
                "operation_id": d.operation_id,
                "reason": d.reason,
            }
            for d in result.drops
        ]
        report["auth"] = {
            "scheme_name": result.auth_plan.scheme_name,
            "provider": result.auth_plan.provider,
            "secret_key": result.auth_plan.secret_key,
            "yaml": result.auth_plan.yaml_block,
            "notes": result.auth_plan.notes,
        }
        report["notes"] = result.notes

    if gate is not None:
        report["gate"] = gate
    if mcp is not None:
        report["mcp"] = mcp
    if wire is not None:
        report["wire"] = wire

    return report


def print_report(report: dict[str, Any]) -> None:
    s = report.get("summary") or {}
    print(f"nw-connector-builder report — id={s.get('id')}")
    print(f"  source: {s.get('spec_source')} ({s.get('spec_version')})")
    if s.get("error"):
        print(f"  ERROR: {s['error']}")
    if "generated" in s:
        print(
            f"  operations: {s.get('generated')}/{s.get('total_operations')} generated, "
            f"{s.get('soft_dropped')} soft-dropped"
        )
        if s.get("coverage_warning"):
            print("  WARNING: usable operations < 50% of document operations")
    for a in report.get("generated_actions") or []:
        print(f"    + {a['method']} {a['path']} → {a['name']}")
    for d in report.get("skipped") or []:
        print(f"    - {d['method']} {d['path']}: {d['reason']}")
    auth = report.get("auth")
    if auth:
        # Do not print secret_key (env name) — keep it in report.json only.
        print(f"  auth: provider={auth.get('provider')}")
    gate = report.get("gate")
    if gate:
        print(f"  gate: {gate}")
    mcp = report.get("mcp")
    if mcp:
        print(f"  mcp: {mcp}")
    wire = report.get("wire")
    if wire:
        print(f"  wire: {wire}")


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

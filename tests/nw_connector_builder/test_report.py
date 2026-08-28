# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for build report helpers."""

from __future__ import annotations

from pathlib import Path

from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import ActionPlan, DeriveResult, SoftDrop
from nw_connector_builder.report import build_report, print_report, write_report


def _minimal_result() -> DeriveResult:
    action = ActionPlan(
        name="get_pet",
        method="GET",
        path="/pets/{id}",
        operation={},
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema=None,
        use_rest_response_output=True,
        auth=True,
    )
    return DeriveResult(
        actions=[action],
        drops=[SoftDrop("POST", "/oauth", "oauthLogin", "oauth2 unsupported")],
        auth_plan=ConnectorAuthPlan(
            scheme_name="ApiKey",
            scheme={"type": "apiKey", "in": "header", "name": "X-API-Key"},
            provider="static_token",
            secret_key="PET_STORE_API_KEY",
            yaml_block={"provider": "static_token", "secret_key": "PET_STORE_API_KEY"},
            notes=[],
        ),
        default_base_url="https://api.example.com",
        coverage_warning=True,
        total_operations=4,
        notes=["note-a"],
    )


def test_build_report_shape() -> None:
    report = build_report(
        connector_id="pet_store",
        meta={
            "origin": "spec.yaml",
            "spec_version": "3.0.3",
            "content_hash": "abc",
        },
        result=_minimal_result(),
        gate={"ok": True},
        mcp={"ok": False, "error": "skip"},
        wire={"ok": True},
    )
    assert report["summary"]["id"] == "pet_store"
    assert report["summary"]["generated"] == 1
    assert report["summary"]["soft_dropped"] == 1
    assert report["summary"]["coverage_warning"] is True
    assert report["generated_actions"][0]["name"] == "get_pet"
    assert report["skipped"][0]["reason"] == "oauth2 unsupported"
    assert report["auth"]["secret_key"] == "PET_STORE_API_KEY"
    assert report["gate"]["ok"] is True
    assert report["mcp"]["error"] == "skip"
    assert report["wire"]["ok"] is True


def test_build_report_error_only() -> None:
    report = build_report(
        connector_id="x",
        meta={"origin": "u", "spec_version": None, "content_hash": None},
        result=None,
        error="boom",
    )
    assert report["summary"]["error"] == "boom"
    assert "generated_actions" not in report


def test_print_report_hides_secret_key(capsys) -> None:
    report = build_report(
        connector_id="pet_store",
        meta={"origin": "s", "spec_version": "3.0.3", "content_hash": "h"},
        result=_minimal_result(),
    )
    print_report(report)
    out = capsys.readouterr().out
    assert "provider=static_token" in out
    assert "PET_STORE_API_KEY" not in out
    assert "WARNING: usable operations < 50%" in out
    assert "+ GET /pets/{id}" in out
    assert "- POST /oauth" in out


def test_write_report(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "report.json"
    report = {"summary": {"id": "x"}}
    write_report(report, path)
    assert path.is_file()
    assert '"id": "x"' in path.read_text(encoding="utf-8")

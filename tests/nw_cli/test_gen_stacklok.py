# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``nw gen-stacklok``: stage order and the exact arguments each stage receives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from nw_cli.cli import app
from nw_cli.stacklok import (
    StacklokScope,
    materialize_spec,
    read_scope,
    run_stacklok_generate,
    run_stacklok_wheel_build,
    stacklok_packages,
    strict_openapi30,
)
from nw_cli.stages import StageError

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[1] / "nw_stacklok_builder" / "fixtures" / "stacklok"


def _scope(tmp_path: Path, **overrides: Any) -> Path:
    doc: Dict[str, Any] = yaml.safe_load((FIXTURES / "petstore.yaml").read_text())
    doc["spec"]["source"] = "openapi.json"
    doc.update(overrides)
    (tmp_path / "openapi.json").write_text(
        (FIXTURES / "petstore_openapi.json").read_text(), encoding="utf-8"
    )
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    return root


def _invoke(root: Path, args: list[str], calls: list[tuple[str, Any]]) -> Any:
    def fake_run_build(**kwargs: Any) -> int:
        calls.append(("connector", kwargs))
        return 0

    def fake_wheels(*args: Any, **kwargs: Any) -> None:
        calls.append(("wheel", (args, kwargs)))

    def fake_generate(*args: Any, **kwargs: Any) -> Path:
        calls.append(("stacklok", (args, kwargs)))
        return root / "out" / "petstore-mcp"

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_connector_builder.pipeline.run_build", side_effect=fake_run_build),
        patch("nw_cli.stacklok.run_stacklok_wheel_build", side_effect=fake_wheels),
        patch("nw_cli.stacklok.run_stacklok_generate", side_effect=fake_generate),
    ):
        return runner.invoke(app, ["gen-stacklok", *args])


def test_stages_run_in_order_with_exact_arguments(fake_root: Path, tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    calls: list[tuple[str, Any]] = []

    result = _invoke(fake_root, ["--scope", str(scope), "--connector-id", "pet_store"], calls)

    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["connector", "wheel", "stacklok"]
    assert calls[0][1] == {
        "spec": str((tmp_path / "openapi.json").resolve()),
        "connector_id": "pet_store",
        "node_wire_root": fake_root,
        "wire": True,
        "force": False,
        "no_mcp": True,
        "base_url": "https://petstore3.swagger.io/api/v3",
    }
    wheel_args, wheel_kwargs = calls[1][1]
    assert wheel_args == (fake_root, "pet_store")
    assert set(wheel_kwargs) == {"log"}
    gen_args, gen_kwargs = calls[2][1]
    assert gen_args[0] == fake_root
    assert isinstance(gen_args[1], StacklokScope) and gen_args[1].connector_id == "pet_store"
    assert gen_kwargs["output_dir"] == fake_root / "nw-stacklok-builder" / "out"
    assert gen_kwargs["force_output"] is False
    assert gen_kwargs["lock"] is True
    assert "MCP server ready" in result.output


def test_flags_reach_the_stages(fake_root: Path, tmp_path: Path) -> None:
    scope = _scope(tmp_path, runtime={"type": "node_wire", "connector_id": "pet_store"})
    calls: list[tuple[str, Any]] = []

    result = _invoke(
        fake_root,
        [
            "--scope",
            str(scope),
            "--force",
            "--no-wheel",
            "--no-lock",
            "--output-dir",
            str(tmp_path),
        ],
        calls,
    )

    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["connector", "stacklok"]
    assert calls[0][1]["force"] is True
    _, gen_kwargs = calls[1][1]
    assert gen_kwargs["output_dir"] == tmp_path
    assert gen_kwargs["force_output"] is True
    assert gen_kwargs["lock"] is False


def test_connector_id_mismatch_is_a_clean_error(fake_root: Path, tmp_path: Path) -> None:
    scope = _scope(tmp_path, runtime={"type": "node_wire", "connector_id": "pet_store"})
    calls: list[tuple[str, Any]] = []
    result = _invoke(fake_root, ["--scope", str(scope), "--connector-id", "other"], calls)
    assert result.exit_code == 1
    assert "does not match" in result.output
    assert calls == []


def test_connector_id_is_required_without_a_runtime_block(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke(fake_root, ["--scope", str(_scope(tmp_path))], calls)
    assert result.exit_code == 1
    assert "--connector-id" in result.output


def test_stage_failure_is_a_clean_error(fake_root: Path, tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build", return_value=0),
        patch(
            "nw_cli.stacklok.run_stacklok_wheel_build",
            side_effect=StageError("Wheel build failed (exit 1)"),
        ),
    ):
        result = runner.invoke(
            app, ["gen-stacklok", "--scope", str(scope), "--connector-id", "pet_store"]
        )
    assert result.exit_code == 1
    assert "Wheel build failed" in result.output
    assert "Traceback" not in result.output


def test_read_scope_binds_the_runtime_block(tmp_path: Path) -> None:
    scoped = read_scope(_scope(tmp_path), "pet_store")
    assert scoped.document["runtime"] == {"type": "node_wire", "connector_id": "pet_store"}
    assert scoped.server_name == "petstore"
    written = yaml.safe_load(scoped.write(tmp_path / "work").read_text())
    assert written["runtime"]["connector_id"] == "pet_store"


def test_materialize_spec_converts_swagger2(tmp_path: Path) -> None:
    swagger = tmp_path / "swagger.json"
    swagger.write_text(
        json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "t", "version": "1"},
                "host": "api.example.test",
                "basePath": "/v1",
                "paths": {"/pets": {"get": {"responses": {"200": {"description": "ok"}}}}},
            }
        ),
        encoding="utf-8",
    )
    out = materialize_spec(str(swagger), tmp_path / "work")
    doc = json.loads(out.read_text())
    assert doc["openapi"].startswith("3.")
    assert "/pets" in doc["paths"]
    # An OpenAPI 3 file is used as-is.
    assert materialize_spec(str(FIXTURES / "petstore_openapi.json"), tmp_path) == (
        FIXTURES / "petstore_openapi.json"
    )


def test_wheel_build_targets_the_stacklok_packages(tmp_path: Path) -> None:
    with patch("nw_cli.stacklok.run_logged_command", return_value=0) as run:
        run_stacklok_wheel_build(tmp_path, "pet_store")
    cmd = run.call_args.args[0]
    assert cmd == [
        "bash",
        "scripts/build-packages.sh",
        "--musllinux",
        *stacklok_packages("pet_store"),
    ]
    assert "packages/toolhive" in cmd


def test_scope_validation_errors_stop_generation(tmp_path: Path) -> None:
    doc = yaml.safe_load((FIXTURES / "petstore.yaml").read_text())
    doc["groups"][0]["tools"][0]["endpoint"] = "GET /pet/findByColor"
    scoped = read_scope(_scope(tmp_path, groups=doc["groups"]), "pet_store")
    out = tmp_path / "out"

    with pytest.raises(StageError, match="Scope validation failed"):
        run_stacklok_generate(tmp_path, scoped, output_dir=out, work_dir=tmp_path / "work")
    assert not out.exists()


def test_strict_openapi30_rewrites_json_schema_forms() -> None:
    doc = {
        "openapi": "3.0.3",
        "components": {
            "schemas": {
                "nullable": {"type": ["string", "null"]},
                "only_null": {"type": "null"},
                "multi": {"type": ["string", "integer"]},
                "tuple": {"type": "array", "items": [{"type": "integer"}, {"type": "null"}]},
                "fine": {"type": "string"},
            }
        },
    }
    assert strict_openapi30(doc) is True
    schemas = doc["components"]["schemas"]
    assert schemas["nullable"] == {"type": "string", "nullable": True}
    assert schemas["only_null"] == {"nullable": True}
    assert schemas["multi"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert schemas["tuple"]["items"] == {"anyOf": [{"type": "integer"}, {"nullable": True}]}
    assert schemas["fine"] == {"type": "string"}
    assert strict_openapi30(doc) is False

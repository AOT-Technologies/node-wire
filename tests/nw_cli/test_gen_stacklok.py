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
        "force": True,  # always rebuilt from the scope's spec
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


@pytest.mark.parametrize("name", ["../../x", "Pet_Store", "-pets", "a/b", "x" * 64])
def test_read_scope_rejects_a_server_name_that_is_not_a_dns_label(
    tmp_path: Path, name: str
) -> None:
    """server.name becomes the output directory that --force replaces."""
    with pytest.raises(StageError, match="invalid server.name"):
        read_scope(_scope(tmp_path, server={"name": name, "description": "d"}), "pet_store")


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


def test_spec_preparation_keeps_openapi31_json_schema_forms(tmp_path: Path) -> None:
    """3.1 allows ``type: [x, "null"]``; only 3.0 documents are rewritten, by both entry points."""
    from nw_cli.stacklok import prepare_spec

    spec = tmp_path / "v31.json"
    schema = {"type": ["string", "null"]}
    spec.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "t", "version": "1"},
                "paths": {},
                "components": {"schemas": {"Name": schema}},
            }
        ),
        encoding="utf-8",
    )
    prepared = json.loads(prepare_spec(str(spec), tmp_path / "out.json").read_text())
    assert prepared["components"]["schemas"]["Name"] == schema
    assert materialize_spec(str(spec), tmp_path / "work") == spec


def test_wheel_build_targets_the_stacklok_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _wheel_root(tmp_path)
    monkeypatch.setenv("NW_WHEEL_ARCHS", "aarch64")
    with patch("nw_cli.stacklok.run_logged_command", return_value=0) as run:
        run_stacklok_wheel_build(root, "demo", log=lambda _: None)
    cmd = run.call_args.args[0]
    assert cmd == ["bash", "scripts/build-packages.sh", "--cibw-linux", *stacklok_packages("demo")]
    assert run.call_args.kwargs["env"]["CIBW_BUILD"] == "cp313-musllinux_*"
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


def _swagger2(tmp_path: Path) -> Path:
    spec = tmp_path / "swagger.json"
    spec.write_text(
        json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "demo", "version": "1"},
                "host": "api.example.test",
                "paths": {
                    "/pets": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "ok",
                                    "schema": {
                                        "type": "object",
                                        "properties": {"n": {"type": ["string", "null"]}},
                                    },
                                }
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return spec


def _invoke_path(
    root: Path,
    spec: Path,
    args: list[str],
    calls: list[tuple[str, Any]],
    *,
    write_scope: bool = True,
    approve: bool | None = None,
) -> Any:
    """`gen-stacklok --path` with Phase 1 and Phase 3 faked; records the calls.

    ``approve``: None = no terminal; True/False = the reviewer's answer.
    """

    def fake_interactive(node_wire_root: Path, request: Any, *, work_dir: Path, model: Any) -> Path:
        calls.append(("scoping-interactive", request))
        scope = work_dir / "mcp-scope.yaml"
        if write_scope:
            work_dir.mkdir(parents=True, exist_ok=True)
            scope.write_text((FIXTURES / "petstore.yaml").read_text(), encoding="utf-8")
        return scope

    def fake_scoping(
        node_wire_root: Path, request: Any, *, work_dir: Path, model: Any, log: Any
    ) -> Path:
        calls.append(("scoping", request))
        scope = work_dir / "mcp-scope.yaml"
        if write_scope:
            work_dir.mkdir(parents=True, exist_ok=True)
            scope.write_text((FIXTURES / "petstore.yaml").read_text(), encoding="utf-8")
        return scope

    def fake_phase3(progress: Any, node_wire_root: Path, scope_file: Path, *rest: Any) -> Path:
        calls.append(("phase3", scope_file))
        return root / "out" / "petstore-mcp"

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_cli.scoping.run_ai_scoping", side_effect=fake_scoping),
        patch("nw_cli.scoping.run_ai_scoping_interactive", side_effect=fake_interactive),
        patch("nw_cli.cli._stacklok_phase3", side_effect=fake_phase3),
        patch("nw_cli.cli.is_interactive", return_value=approve is not None),
        patch("rich.prompt.Confirm.ask", return_value=bool(approve)),
    ):
        return runner.invoke(app, ["gen-stacklok", "--path", str(spec), *args])


def test_path_runs_phases_1_to_3_in_one_command(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke_path(
        fake_root,
        _swagger2(tmp_path),
        [
            "--connector-id",
            "demo",
            "--workflow",
            "Find pets by status",
            "--workflow",
            "Place an order",
            "--auth-hint",
            "api key in the api_key header",
            "--scoping-notes",
            "read-only first",
        ],
        calls,
        approve=True,
    )

    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["scoping-interactive", "phase3"]
    request = calls[0][1]
    prepared = fake_root / "nw-stacklok-builder" / "specs" / "demo.openapi.json"
    assert request.spec == prepared
    assert json.loads(prepared.read_text())["openapi"].startswith("3.0")
    assert request.workflows == ["Find pets by status", "Place an order"]
    assert request.auth_hint == "api key in the api_key header"
    assert request.notes == "read-only first"
    assert calls[1][1] == fake_root / "nw-stacklok-builder" / "scoping" / "demo" / "mcp-scope.yaml"
    assert "MCP server ready" in result.output


def test_without_a_terminal_it_stops_for_review(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke_path(
        fake_root, _swagger2(tmp_path), ["--connector-id", "demo", "--workflow", "w"], calls
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["scoping"]
    assert "Stopped for review" in result.output
    assert "--scope" in result.output and "--connector-id demo" in result.output


def test_declining_the_review_stops_before_generation(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke_path(
        fake_root,
        _swagger2(tmp_path),
        ["--connector-id", "demo", "--workflow", "w"],
        calls,
        approve=False,
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["scoping-interactive"]
    scope = fake_root / "nw-stacklok-builder" / "scoping" / "demo" / "mcp-scope.yaml"
    assert f"uv run nw gen-stacklok --scope {scope} --connector-id demo" in result.output


def test_an_existing_scope_is_reused_unless_rescoped(fake_root: Path, tmp_path: Path) -> None:
    existing = fake_root / "nw-stacklok-builder" / "scoping" / "demo" / "mcp-scope.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text((FIXTURES / "petstore.yaml").read_text(), encoding="utf-8")
    spec = _swagger2(tmp_path)

    calls: list[tuple[str, Any]] = []
    result = _invoke_path(fake_root, spec, ["--connector-id", "demo"], calls, approve=True)
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["phase3"]  # no --workflow needed, no AI run

    calls.clear()
    forced = _invoke_path(
        fake_root, spec, ["--connector-id", "demo", "--rescope"], calls, approve=True
    )
    assert forced.exit_code == 0, forced.output
    assert [name for name, _ in calls] == ["scoping-interactive", "phase3"]  # --rescope redoes it

    calls.clear()
    kept = _invoke_path(fake_root, spec, ["--connector-id", "demo", "--force"], calls, approve=True)
    assert kept.exit_code == 0, kept.output
    assert [name for name, _ in calls] == ["phase3"]  # --force keeps the saved scope


def test_without_workflows_the_ai_proposes_them(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke_path(
        fake_root, _swagger2(tmp_path), ["--connector-id", "demo"], calls, approve=True
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == "scoping-interactive" and calls[0][1].workflows == []


def test_headless_flag_runs_phase_1_unattended_even_with_a_terminal(
    fake_root: Path, tmp_path: Path
) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke_path(
        fake_root,
        _swagger2(tmp_path),
        ["--connector-id", "demo", "--headless"],
        calls,
        approve=True,
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["scoping", "phase3"]


def test_path_mode_usage_errors(fake_root: Path, tmp_path: Path) -> None:
    spec = _swagger2(tmp_path)
    with patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root):
        no_id = runner.invoke(app, ["gen-stacklok", "--path", str(spec), "--workflow", "w"])
        both = runner.invoke(app, ["gen-stacklok", "--scope", "s.yaml", "--path", "p.json"])
        neither = runner.invoke(app, ["gen-stacklok"])
    assert no_id.exit_code == 2 and "--connector-id" in no_id.output
    assert both.exit_code == 2 and neither.exit_code == 2
    assert "exactly one of --path or --scope" in both.output


def test_prepared_spec_builds_the_connector_from_its_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nw_cli.stacklok import prepare_spec

    origin = _swagger2(tmp_path)
    prepared = prepare_spec(str(origin), tmp_path / "specs" / "demo.openapi.json")
    monkeypatch.chdir(tmp_path)
    scope_dir = tmp_path / "scoping-output"
    scope_dir.mkdir()
    scope = _scope(scope_dir)
    doc = yaml.safe_load(scope.read_text())
    doc["spec"]["source"] = "specs/demo.openapi.json"  # relative to the cwd, as the skill writes it
    scope.write_text(yaml.safe_dump(doc), encoding="utf-8")

    scoped = read_scope(scope, "demo")

    assert scoped.spec_source == str(prepared.resolve())
    assert scoped.connector_spec_source == str(origin.resolve())
    assert materialize_spec(scoped.spec_source, tmp_path / "work") == prepared.resolve()


def test_existing_output_fails_fast_without_a_terminal(fake_root: Path, tmp_path: Path) -> None:
    (fake_root / "nw-stacklok-builder" / "out" / "petstore-mcp").mkdir(parents=True)
    calls: list[tuple[str, Any]] = []
    result = _invoke(
        fake_root, ["--scope", str(_scope(tmp_path)), "--connector-id", "pet_store"], calls
    )
    assert result.exit_code == 1
    assert "Output project already exists" in result.output and "--force" in result.output
    assert calls == []  # nothing built before the check


def test_existing_output_is_replaced_after_confirmation(fake_root: Path, tmp_path: Path) -> None:
    (fake_root / "nw-stacklok-builder" / "out" / "petstore-mcp").mkdir(parents=True)
    calls: list[tuple[str, Any]] = []
    with (
        patch("nw_cli.cli.is_interactive", return_value=True),
        patch("rich.prompt.Confirm.ask", return_value=True),
    ):
        result = _invoke(
            fake_root, ["--scope", str(_scope(tmp_path)), "--connector-id", "pet_store"], calls
        )
    assert result.exit_code == 0, result.output
    assert calls[-1][1][1]["force_output"] is True


def test_success_prints_run_commands_with_the_real_names(fake_root: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []
    result = _invoke(
        fake_root, ["--scope", str(_scope(tmp_path)), "--connector-id", "pet_store"], calls
    )
    assert result.exit_code == 0, result.output
    assert "docker build -t petstore-mcp" in result.output
    assert (
        "--name petstore-mcp" in result.output
        and "thv run http://127.0.0.1:8200/mcp" in result.output
    )


def _wheel_root(tmp_path: Path) -> Path:
    """A node-wire root with an Alpine template and four tiny packages (runtime, bindings,
    toolhive, connectors/demo), each compiling one src/ package."""
    root = tmp_path / "nw"
    (root / "nw-stacklok-builder" / "template").mkdir(parents=True)
    (root / "nw-stacklok-builder" / "template" / "Dockerfile").write_text(
        "FROM dhi.io/python:3.13-alpine3.23-dev AS builder\nFROM dhi.io/python:3.13-alpine3.23\n",
        encoding="utf-8",
    )
    for package, module, where in (
        ("packages/runtime", "node_wire_runtime", "../../src"),
        ("packages/bindings", "bindings", "../../src"),
        ("packages/toolhive", "node_wire_toolhive", "../../src"),
        ("packages/connectors/demo", "node_wire_demo", "../../../src"),
    ):
        pkg = root / package
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text(
            f'[tool.setuptools.packages.find]\nwhere = ["{where}"]\ninclude = ["{module}*"]\n',
            encoding="utf-8",
        )
        (root / "src" / module).mkdir(parents=True)
        (root / "src" / module / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _fake_builder(root: Path, built: list[list[str]]) -> Any:
    def run(cmd: list[str], *, cwd: Path, log: Any = None, env: Any = None) -> int:
        packages = cmd[3:]
        built.append(packages)
        assert cmd[2] == "--cibw-linux" and env["CIBW_BUILD"] == "cp313-musllinux_*"
        for package in packages:
            dist = root / package / "dist"
            dist.mkdir(exist_ok=True)
            stem = Path(package).name
            (dist / f"{stem}-1.0-cp313-cp313-musllinux_1_2_aarch64.whl").write_bytes(b"")
        return 0

    return run


def test_unchanged_wheels_are_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _wheel_root(tmp_path)
    monkeypatch.setenv("NW_WHEEL_ARCHS", "aarch64")
    built: list[list[str]] = []
    lines: list[str] = []
    with patch("nw_cli.stacklok.run_logged_command", side_effect=_fake_builder(root, built)):
        first = run_stacklok_wheel_build(root, "demo", log=lines.append)
        second = run_stacklok_wheel_build(root, "demo", log=lines.append)
        (root / "src" / "node_wire_demo" / "__init__.py").write_text("x = 2\n", encoding="utf-8")
        third = run_stacklok_wheel_build(root, "demo", log=lines.append)

    assert first == stacklok_packages("demo")
    assert second == []  # nothing changed: no compile at all
    assert third == ["packages/connectors/demo"]  # only the changed package
    assert built == [stacklok_packages("demo"), ["packages/connectors/demo"]]
    assert any("Target: cp313 musllinux" in line for line in lines)
    assert any(line.startswith("Up to date, reused:") for line in lines)


def test_a_missing_architecture_forces_a_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _wheel_root(tmp_path)
    built: list[list[str]] = []
    with patch("nw_cli.stacklok.run_logged_command", side_effect=_fake_builder(root, built)):
        monkeypatch.setenv("NW_WHEEL_ARCHS", "aarch64")
        run_stacklok_wheel_build(root, "demo", log=lambda _: None)
        monkeypatch.setenv("NW_WHEEL_ARCHS", "aarch64 x86_64")
        again = run_stacklok_wheel_build(root, "demo", log=lambda _: None)
    assert again == stacklok_packages("demo")  # no x86_64 wheels yet

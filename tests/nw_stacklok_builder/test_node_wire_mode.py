# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The vendored stacklok generator in node-wire mode (scope ``runtime: node_wire``)."""

from __future__ import annotations

import json
import logging
import shutil
import tomllib
import zipfile
from pathlib import Path

import pytest
import yaml

from mcp_builder.pipeline import run_pipeline
from nw_stacklok.hooks import NodeWireOptions
from nw_stacklok.project import TemplateChangedError, WheelsMissingError, finish_project
from node_wire_toolhive import request as toolhive_request
from nw_stacklok.resolve import NodeWireResolveError

from .conftest import CONNECTOR_ID, FIXTURES, PETSTORE_SPEC, TEMPLATE, NodeWireCheckout


def _generate(
    checkout: NodeWireCheckout, tmp_path: Path, *, wheels: bool = False, **scope_overrides
) -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return run_pipeline(
        checkout.scope(tmp_path, **scope_overrides),
        PETSTORE_SPEC,
        TEMPLATE,
        out,
        node_wire=NodeWireOptions(checkout.root, wheels=wheels),
    )


def test_tools_run_connector_actions(node_wire_checkout: NodeWireCheckout, tmp_path: Path) -> None:
    project = _generate(node_wire_checkout, tmp_path)
    module = project / "src" / "petstore_mcp"

    tools = (module / "api" / "tools.py").read_text()
    assert 'self._client.run(\n            "get_pet_by_id",\n            {"petid": petId},' in tools
    assert '"place_order",\n            {"petid": petId, "quantity": quantity},' in tools
    assert '"find_pets_by_status",\n            {"status": status},' in tools
    assert "self._client.request" not in tools
    # Signatures and descriptions are stacklok's own.
    assert (
        'petId: Annotated[int, Field(description="The numeric ID of the pet to retrieve.")]'
        in tools
    )

    client = (module / "client.py").read_text()
    assert "import httpx" not in client
    assert "NodeWireClient(CONNECTOR_ID" in client and f'CONNECTOR_ID = "{CONNECTOR_ID}"' in client

    registrations = (module / "api" / "mcp_builder.py").read_text()
    assert "tools = Tools(APIClient())" in registrations
    assert "register_config_tools(mcp, tools._client.node_wire)" in registrations
    # Wraps every registered tool, config tools included, so it comes last.
    order = [
        registrations.index(line)
        for line in (
            "mcp.add_tool(tools.find_pets_by_status)",
            "register_config_tools(mcp, tools._client.node_wire)",
            "report_call_errors(mcp, tools._client.node_wire)",
            "return mcp",
        )
    ]
    assert order == sorted(order)
    for path in project.rglob("*.py"):
        compile(path.read_text(), str(path), "exec")


def test_project_packaging(node_wire_checkout: NodeWireCheckout, tmp_path: Path) -> None:
    project = _generate(node_wire_checkout, tmp_path)

    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    assert deps[:4] == [
        "node-wire-runtime",
        "node-wire-bindings",
        "node-wire-toolhive",
        "node-wire-pet-store",
    ]
    assert not any(d.startswith("httpx") for d in deps)  # conflicts with the runtime's pin

    config = yaml.safe_load((project / "config" / "connectors.yaml").read_text())
    assert list(config["connectors"]) == [CONNECTOR_ID]
    assert "mcp" in config["connectors"][CONNECTOR_ID]["exposed_via"]
    example = yaml.safe_load((project / "config" / "tenants.example.yaml").read_text())
    doc = example["tenants"]["example-tenant"][CONNECTOR_ID][0]
    assert doc["auth"]["header_name"] == "api_key"
    assert "petstore_auth" in doc["auth_schemes"]

    dockerfile = (project / "Dockerfile").read_text()
    assert "dhi.io/python:3.13-alpine" in dockerfile  # stacklok's base image, unchanged
    assert "COPY config/ /app/config/" in dockerfile
    assert f"NW_UPSTREAM_BEARER_CONNECTORS={CONNECTOR_ID}" in dockerfile
    assert "NW_MULTITENANCY_ENABLED=true" in dockerfile
    ignored = (project / ".dockerignore").read_text().splitlines()
    # Allow-list, so a tenants file under any name (tenants.prod.yaml) stays out of the image.
    assert ignored[-2:] == ["config/*", "!config/connectors.yaml"]
    assert 'extra="ignore"' in (project / "src" / "petstore_mcp" / "settings.py").read_text()


def test_runtime_log_fields_and_telemetry_are_wired(
    node_wire_checkout: NodeWireCheckout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The runtime logs its taxonomy/trace fields as ``extra``; the server must print them."""
    project = _generate(node_wire_checkout, tmp_path)
    module = project / "src" / "petstore_mcp"

    main = (module / "__main__.py").read_text()
    assert "from node_wire_toolhive import init_telemetry" in main
    assert main.index("configure_logging(log_level=log_level)") < main.index(
        'init_telemetry("petstore-mcp")'
    )

    monkeypatch.syspath_prepend(str(project / "src"))
    from petstore_mcp.configure_logging import configure_logging  # type: ignore[import-not-found]

    root = logging.getLogger()
    saved = (list(root.handlers), list(root.filters), root.level)
    try:
        configure_logging("INFO", colored_logs=False)
        logging.getLogger("runtime.base_connector").error(
            "Connector execution failed",
            extra={"trace_id": "t-1", "error_code": "AUTH_FAILED", "error_category": "AUTH"},
        )
    finally:
        root.handlers[:] = saved[0]
        root.filters[:] = saved[1]
        root.setLevel(saved[2])
    err = capsys.readouterr().err
    assert "trace_id=t-1" in err
    assert "error_code=AUTH_FAILED" in err
    assert "error_category=AUTH" in err


@pytest.mark.parametrize(
    ("relative", "old"),
    [
        ("Dockerfile", "COPY --from=builder /app/src /app/src\n"),
        ("src/petstore_mcp/api/mcp_builder.py", "return mcp"),
        ("src/petstore_mcp/settings.py", 'env_file_encoding="utf-8",'),
        ("src/petstore_mcp/__main__.py", 'if __name__ == "__main__":'),
        ("src/petstore_mcp/__main__.py", "configure_logging(log_level=log_level)\n"),
        ("src/petstore_mcp/configure_logging.py", "structlog.stdlib.add_logger_name,\n"),
    ],
)
def test_a_changed_stacklok_template_fails_the_build(
    node_wire_checkout: NodeWireCheckout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    old: str,
) -> None:
    """Every patch must match; a silent no-op would ship an image missing node-wire wiring."""
    captured = {}
    monkeypatch.setattr(
        "nw_stacklok.project.finish_project",
        lambda project_dir, plan, **kwargs: captured.update(plan=plan),
    )
    project = _generate(node_wire_checkout, tmp_path)  # stacklok's output, not yet finished
    target = project / relative
    target.write_text(target.read_text().replace(old, "# changed upstream"))

    with pytest.raises(TemplateChangedError, match=target.name):
        finish_project(project, captured["plan"], wheels=False, lock=False)


def test_manifests_one_backend_and_a_proxy_per_tenant(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path
) -> None:
    project = _generate(node_wire_checkout, tmp_path)
    deploy = project / "deploy"

    names = {p.name for p in deploy.iterdir()}
    assert "mcpserver.yaml" not in names
    assert {
        "backend.yaml",
        "networkpolicy.yaml",
        "proxy-secret.yaml",
        "tenant-proxy.yaml",
        "tenants-secret.yaml",
    } <= names
    assert {"mcpexternalauthconfig.yaml", "secret.yaml"} <= names  # stacklok's api_key auth

    deployment, service = yaml.safe_load_all((deploy / "backend.yaml").read_text())
    assert deployment["kind"] == "Deployment" and service["kind"] == "Service"
    labels = deployment["spec"]["template"]["metadata"]["labels"]

    (policy,) = yaml.safe_load_all((deploy / "networkpolicy.yaml").read_text())
    assert policy["spec"]["podSelector"]["matchLabels"] == labels
    allowed = policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]

    (proxy,) = yaml.safe_load_all((deploy / "tenant-proxy.yaml").read_text())
    assert proxy["kind"] == "MCPRemoteProxy"
    spec = proxy["spec"]
    assert spec["headerForward"]["addPlaintextHeaders"] == {"X-Tenant-ID": "REPLACE_ME_TENANT"}

    # Second layer behind the NetworkPolicy: the proxy sends the secret the backend checks.
    (secret,) = yaml.safe_load_all((deploy / "proxy-secret.yaml").read_text())
    ref = {"name": secret["metadata"]["name"], "key": "token"}
    assert spec["headerForward"]["addHeadersFromSecret"] == [
        {"headerName": "X-NW-Proxy-Secret", "valueSecretRef": ref}
    ]
    env = {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["NW_PROXY_SECRET"]["valueFrom"]["secretKeyRef"] == ref
    # The backend (node_wire_toolhive) refuses the shipped placeholder, so it must match.
    assert secret["stringData"]["token"] == toolhive_request.PROXY_SECRET_PLACEHOLDER
    assert "NW_PROXY_SECRET" == toolhive_request.PROXY_SECRET_ENV
    assert "X-NW-Proxy-Secret".lower() == toolhive_request.PROXY_SECRET_HEADER
    assert spec["remoteUrl"].startswith("http://petstore-backend.")
    assert spec["allowPrivateEndpoint"] is True
    assert spec["externalAuthConfigRef"] == {"name": "petstore-auth"}
    assert spec["podTemplateSpec"]["metadata"]["labels"] == allowed

    for path in deploy.glob("*.yaml"):
        assert "passthroughHeaders" not in path.read_text()


def _checkout_copy(checkout: NodeWireCheckout, tmp_path: Path) -> NodeWireCheckout:
    root = tmp_path / "node-wire"
    shutil.copytree(checkout.root, root)
    return NodeWireCheckout(root)


def test_endpoint_the_connector_lacks_is_reported(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path
) -> None:
    checkout = _checkout_copy(node_wire_checkout, tmp_path)
    report_file = checkout.root / "packages" / "connectors" / CONNECTOR_ID / "report.json"
    report = json.loads(report_file.read_text())
    report["generated_actions"] = [
        a for a in report["generated_actions"] if a["name"] not in ("get_pet_by_id", "place_order")
    ]
    report["skipped"] = [
        {"method": "POST", "path": "/store/order", "operation_id": "placeOrder", "reason": "cookie"}
    ]
    report_file.write_text(json.dumps(report))

    with pytest.raises(NodeWireResolveError) as excinfo:
        _generate(checkout, tmp_path)

    assert excinfo.value.problems == [
        "get_pet_by_id: GET /pet/{petId} is not an operation of connector 'pet_store'",
        "place_order: POST /store/order was skipped by nw-connector-builder (cookie)",
    ]


def test_missing_required_parameter_is_reported(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path
) -> None:
    scope = yaml.safe_load((FIXTURES / "petstore.yaml").read_text())
    find_by_status = scope["groups"][0]["tools"][0]
    find_by_status["parameters"][0]["required"] = False
    find_by_status["parameters"][0]["name"] = "colour"
    with pytest.raises(NodeWireResolveError) as excinfo:
        _generate(node_wire_checkout, tmp_path, groups=scope["groups"])
    message = str(excinfo.value)
    assert "parameter 'colour' (query) is not an input of action 'find_pets_by_status'" in message


def test_runtime_block_requires_node_wire_options(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match="nw gen-stacklok"):
        run_pipeline(node_wire_checkout.scope(tmp_path), PETSTORE_SPEC, TEMPLATE, out)


def _fake_wheel(dist: Path, dist_name: str, arch: str) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    stem = dist_name.replace("-", "_")
    with zipfile.ZipFile(dist / f"{stem}-1.1.0-cp313-cp313-musllinux_1_2_{arch}.whl", "w") as zf:
        zf.writestr(f"{stem}-1.1.0.dist-info/METADATA", "")


def test_wheels_are_pinned_per_architecture(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path
) -> None:
    checkout = _checkout_copy(node_wire_checkout, tmp_path)
    packages = checkout.root / "packages"
    dists = {
        "node-wire-runtime": packages / "runtime" / "dist",
        "node-wire-bindings": packages / "bindings" / "dist",
        "node-wire-toolhive": packages / "toolhive" / "dist",
        "node-wire-pet-store": packages / "connectors" / CONNECTOR_ID / "dist",
    }
    for name, dist in dists.items():
        for arch in ("aarch64", "x86_64"):
            _fake_wheel(dist, name, arch)
    _fake_wheel(dists["node-wire-runtime"], "node-wire-runtime", "armv7l")  # not shared by all

    project = _generate(checkout, tmp_path, wheels=True)

    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    assert pyproject["tool"]["uv"]["environments"] == [
        "sys_platform == 'linux' and platform_machine == 'aarch64'",
        "sys_platform == 'linux' and platform_machine == 'x86_64'",
    ]
    runtime_sources = pyproject["tool"]["uv"]["sources"]["node-wire-runtime"]
    assert [s["marker"] for s in runtime_sources] == [
        "platform_machine == 'aarch64'",
        "platform_machine == 'x86_64'",
    ]
    for source in runtime_sources:
        assert (project / source["path"]).is_file()
    assert "COPY wheels/ ./wheels/" in (project / "Dockerfile").read_text()


def test_missing_wheels_are_reported(node_wire_checkout: NodeWireCheckout, tmp_path: Path) -> None:
    checkout = _checkout_copy(node_wire_checkout, tmp_path)
    for dist in (checkout.root / "packages").rglob("dist"):
        shutil.rmtree(dist)
    with pytest.raises(WheelsMissingError, match="No cp313 musllinux .* wheel for"):
        _generate(checkout, tmp_path, wheels=True)

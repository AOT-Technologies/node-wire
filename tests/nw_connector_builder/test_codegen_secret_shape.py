# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated connectors must declare their tenant-secret shape at import time.

Hand-written connectors are hard-coded in
``node_wire_runtime.tenant_persistence``; anything generated has to declare
itself or the config store refuses its credentials under
``NW_SECRET_SHAPE_POLICY=enforce``.
"""

from __future__ import annotations

import ast

from nw_connector_builder.codegen import generate_logic_module
from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import ActionPlan, DeriveResult


def _action(name: str = "ping", auth_scheme_name: str | None = None) -> ActionPlan:
    return ActionPlan(
        name=name,
        method="GET",
        path=f"/{name}",
        operation={},
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema=None,
        use_rest_response_output=True,
        auth=True,
        auth_scheme_name=auth_scheme_name,
    )


def _host_supplied_plan(secret_key: str) -> ConnectorAuthPlan:
    return ConnectorAuthPlan(
        scheme_name="slackAuth",
        scheme={"type": "oauth2", "flows": {}},
        provider="static_token",
        secret_key=secret_key,
        yaml_block={"provider": "static_token", "secret_key": secret_key},
        notes=[],
        secret_keys=[secret_key],
        tier="host_supplied",
    )


def _result(auth_plan: ConnectorAuthPlan, **kwargs: object) -> DeriveResult:
    return DeriveResult(
        actions=[_action()],
        drops=[],
        auth_plan=auth_plan,
        default_base_url="https://slack.com/api",
        coverage_warning=False,
        total_operations=1,
        **kwargs,  # type: ignore[arg-type]
    )


def _declare_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "declare_secret_shape"
    ]
    assert len(calls) == 1, f"expected exactly one declare_secret_shape call, got {len(calls)}"
    return calls[0]


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_generated_logic_declares_host_supplied_secret() -> None:
    src = generate_logic_module("slack_web", _result(_host_supplied_plan("SLACK_WEB_ACCESS_TOKEN")))
    assert "from node_wire_runtime.tenant_persistence import declare_secret_shape" in src

    call = _declare_call(src)
    assert [ast.literal_eval(a) for a in call.args] == ["slack_web"]
    assert ast.literal_eval(_kwarg(call, "required")) == ["SLACK_WEB_ACCESS_TOKEN"]
    assert ast.literal_eval(_kwarg(call, "formats")) == {"SLACK_WEB_ACCESS_TOKEN": "opaque_secret"}


def test_extra_scheme_secrets_are_format_checked_but_not_required() -> None:
    """A host must be able to store the default credential alone."""
    result = _result(
        _host_supplied_plan("ACME_ACCESS_TOKEN"),
        extra_auth_plans={"partner": _host_supplied_plan("ACME_PARTNER_ACCESS_TOKEN")},
    )
    call = _declare_call(generate_logic_module("acme", result))
    assert ast.literal_eval(_kwarg(call, "required")) == ["ACME_ACCESS_TOKEN"]
    assert ast.literal_eval(_kwarg(call, "formats")) == {
        "ACME_ACCESS_TOKEN": "opaque_secret",
        "ACME_PARTNER_ACCESS_TOKEN": "opaque_secret",
    }


def test_anonymous_connector_declares_no_secrets_explicitly() -> None:
    """``declare_secret_shape(cid)`` states 'no tenant secrets' — not the same as silence."""
    anonymous = ConnectorAuthPlan(None, None, "none", "", {}, [])
    call = _declare_call(generate_logic_module("open_api", _result(anonymous)))
    assert [ast.literal_eval(a) for a in call.args] == ["open_api"]
    assert call.keywords == []


def test_generated_package_readme_states_the_credential_contract() -> None:
    """The host has to learn which secret to set from the package, not just report.json."""
    from nw_connector_builder.codegen import generate_package_readme

    result = _result(_host_supplied_plan("SLACK_WEB_ACCESS_TOKEN"))
    readme = generate_package_readme("slack_web", result)
    assert readme.startswith("# SPDX-FileCopyrightText")  # REUSE lint runs over the repo
    assert "SLACK_WEB_ACCESS_TOKEN" in readme
    assert "Host-supplied credential" in readme
    assert "declare_secret_shape()" in readme
    assert "| `ping` | GET | `/ping` |" in readme


def test_format_entries_carry_a_bandit_marker() -> None:
    """Generated packages land in src/; an unmarked B105 would fail the repo scan."""
    src = generate_logic_module("slack_web", _result(_host_supplied_plan("SLACK_WEB_ACCESS_TOKEN")))
    line = next(ln for ln in src.splitlines() if "'SLACK_WEB_ACCESS_TOKEN': 'opaque_secret'" in ln)
    assert line.rstrip().endswith("# nosec B105")

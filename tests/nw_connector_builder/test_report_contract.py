# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""report.json shape that nw-stacklok-builder relies on to map scope endpoints to actions.

``nw_stacklok.connector.read_connector`` reads ``generated_actions[].{name,method,path}`` and
``skipped[].{method,path,reason}``; changing those keys breaks ``nw gen-stacklok``.
"""

from __future__ import annotations

import json
from pathlib import Path

from nw_connector_builder.pipeline import run_build

FIXTURES = Path(__file__).parent / "fixtures"


def test_report_lists_each_action_with_method_and_path(tmp_path: Path) -> None:
    root = tmp_path / "node-wire"
    for sub in ("src", "packages/connectors", "config", "nw-mcp-builder"):
        (root / sub).mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("", encoding="utf-8")

    assert (
        run_build(
            spec=str(FIXTURES / "demo_pets.openapi.yaml"),
            connector_id="demo_pets",
            node_wire_root=root,
            no_mcp=True,
        )
        == 0
    )

    report = json.loads((root / "packages/connectors/demo_pets/report.json").read_text())
    actions = report["generated_actions"]
    assert actions
    for action in actions:
        assert isinstance(action["name"], str) and action["name"]
        assert action["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        assert action["path"].startswith("/")
    for dropped in report["skipped"]:
        assert {"method", "path", "reason"} <= set(dropped)

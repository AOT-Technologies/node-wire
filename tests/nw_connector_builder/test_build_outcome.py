# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Typed build outcome and the one idempotent wiring function (4.5)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nw_connector_builder.pipeline import BuildError, BuildOutcome, run_build
from nw_connector_builder.wire import WireError, wire_connector_from_report

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = str(FIXTURES / "demo_pets.openapi.yaml")
CID = "demo_pets"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    for sub in ("src", "packages/connectors", "config", "nw-mcp-builder", "scripts"):
        (root / sub).mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")
    (root / "scripts" / "build-packages.sh").write_text(
        "#!/bin/bash\nALL_PACKAGES=(\n  packages/runtime\n)\n", encoding="utf-8"
    )
    return root


def _wired_files(root: Path) -> dict[str, str]:
    return {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in ("config/connectors.yaml", "sample.env", "scripts/build-packages.sh")
    }


def test_a_clean_build_returns_a_typed_outcome(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outcome = run_build(spec=SPEC, connector_id=CID, node_wire_root=root, no_mcp=True, wire=True)

    assert isinstance(outcome, BuildOutcome)
    assert outcome.promoted and outcome.stage_reached == "wired"
    assert outcome.exit_code == 0
    assert outcome.wire == {"ok": True, "wired": True, "all_packages": "added"}
    assert outcome.report_path == root / "packages" / "connectors" / CID / "report.json"
    assert json.loads(outcome.report_path.read_text())["summary"]["id"] == CID


def test_a_wiring_failure_is_a_promoted_outcome_with_exit_code_1(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with patch(
        "nw_connector_builder.pipeline.wire_connector",
        side_effect=WireError("sample.env is read-only"),
    ):
        outcome = run_build(
            spec=SPEC, connector_id=CID, node_wire_root=root, no_mcp=True, wire=True
        )
    assert outcome.promoted and outcome.stage_reached == "promoted"
    assert outcome.exit_code == 1
    assert outcome.wire == {"ok": False, "error": "sample.env is read-only"}


def test_an_abort_names_where_its_report_went(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: openapi\n", encoding="utf-8")
    with pytest.raises(BuildError) as excinfo:
        run_build(
            spec=str(bad), connector_id=CID, node_wire_root=root, report_path=tmp_path / "r.json"
        )
    assert excinfo.value.report_path == tmp_path / "r.json"
    assert excinfo.value.report_path.is_file()


def test_the_report_is_stamped_with_the_package_source_hash(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import package_source_hash

    root = _root(tmp_path)
    outcome = run_build(spec=SPEC, connector_id=CID, node_wire_root=root, no_mcp=True)
    summary = json.loads(outcome.report_path.read_text())["summary"]
    assert summary["source_hash"] == package_source_hash(root, f"packages/connectors/{CID}")


def test_wiring_is_idempotent_and_matches_the_build(tmp_path: Path) -> None:
    root = _root(tmp_path)
    run_build(spec=SPEC, connector_id=CID, node_wire_root=root, no_mcp=True, wire=True)
    after_build = _wired_files(root)
    assert f"  packages/connectors/{CID}\n" in after_build["scripts/build-packages.sh"]

    assert wire_connector_from_report(root, CID)["all_packages"] == "present"
    wire_connector_from_report(root, CID)

    assert _wired_files(root) == after_build


def test_wiring_from_the_report_needs_a_built_connector(tmp_path: Path) -> None:
    with pytest.raises(WireError, match="no build report"):
        wire_connector_from_report(_root(tmp_path), CID)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_git_ignored_connector_is_not_added_to_all_packages(tmp_path: Path) -> None:
    """Local codegen test builds (slack_web) are gitignored; ALL_PACKAGES is tracked."""
    root = _root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text(f"/packages/connectors/{CID}/\n", encoding="utf-8")

    outcome = run_build(spec=SPEC, connector_id=CID, node_wire_root=root, no_mcp=True, wire=True)

    assert outcome.wire["all_packages"] == "skipped (git-ignored)"
    assert CID not in (root / "scripts" / "build-packages.sh").read_text(encoding="utf-8")
    assert CID in (root / "config" / "connectors.yaml").read_text(encoding="utf-8")

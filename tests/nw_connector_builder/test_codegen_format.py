# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated sources are ruff-formatted before they leave staging.

Generated code lands in ``src/`` beside hand-written code and is held to the
same ``ruff format --check``. Source ruff cannot parse is a codegen bug, so it
fails the build instead of shipping.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from nw_connector_builder.codegen import (
    CodegenFormatError,
    format_generated_sources,
    ruff_config_for,
    write_staging,
)
from nw_connector_builder.derive.operations import derive_operations
from nw_connector_builder.load import load_openapi_document

FIXTURES = Path(__file__).parent / "fixtures"
_REPO = Path(__file__).resolve().parents[2]


def _ruff_check(*paths: Path, config: Path | None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "ruff", "format", "--check", "--no-cache"]
    if config is not None:
        cmd += ["--config", str(config)]
    return subprocess.run(  # noqa: S603 — fixed argv, test-only
        [*cmd, *map(str, paths)], capture_output=True, text=True, check=False
    )


def test_staged_package_passes_ruff_format_check(tmp_path: Path) -> None:
    doc, _ = load_openapi_document(str(FIXTURES / "slack_web.openapi.yaml"))
    result = derive_operations(doc, connector_id="slack_web")
    config = ruff_config_for(_REPO)
    write_staging(tmp_path, "slack_web", result, {}, ruff_config=config)
    proc = _ruff_check(
        tmp_path / "src" / "node_wire_slack_web",
        tmp_path / "packages" / "connectors" / "slack_web" / "tests",
        config=config,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_unparseable_generated_source_fails_the_build(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n", encoding="utf-8")
    with pytest.raises(CodegenFormatError, match="broken.py"):
        format_generated_sources([broken], config=None)


def test_ruff_config_is_the_target_roots_pyproject_when_it_configures_ruff(
    tmp_path: Path,
) -> None:
    assert ruff_config_for(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert ruff_config_for(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    assert ruff_config_for(tmp_path) == tmp_path / "pyproject.toml"

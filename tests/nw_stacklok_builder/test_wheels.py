# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""WheelTarget: the wheel flavour an image needs, read from its Dockerfile."""

from __future__ import annotations

from pathlib import Path

import pytest

from nw_stacklok.wheels import UnknownImageError, WheelTarget

from .conftest import TEMPLATE


def _dockerfile(tmp_path: Path, *froms: str) -> Path:
    path = tmp_path / "Dockerfile"
    path.write_text("".join(f"FROM {f}\nRUN true\n" for f in froms), encoding="utf-8")
    return path


def test_stacklok_template_needs_cp313_musllinux() -> None:
    target = WheelTarget.from_dockerfile(TEMPLATE / "Dockerfile")
    assert (target.python, target.libc) == ("cp313", "musllinux")
    assert target.cibw_build == "cp313-musllinux_*"


def test_the_runtime_stage_decides(tmp_path: Path) -> None:
    dockerfile = _dockerfile(
        tmp_path, "python:3.12-alpine AS builder", "python:3.13-slim@sha256:ab"
    )
    target = WheelTarget.from_dockerfile(dockerfile)
    assert (target.python, target.libc) == ("cp313", "manylinux")
    assert target.description == "cp313 manylinux (for python:3.13-slim)"


def test_unknown_base_image_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(UnknownImageError, match="Python version"):
        WheelTarget.from_dockerfile(_dockerfile(tmp_path, "gcr.io/distroless/base"))


def test_wheels_by_arch_matches_only_the_target(tmp_path: Path) -> None:
    for name in (
        "demo-1.0-cp313-cp313-musllinux_1_2_aarch64.whl",
        "demo-1.0-cp313-cp313-musllinux_1_2_x86_64.whl",
        "demo-1.0-cp313-cp313-manylinux_2_17_x86_64.whl",
        "demo-1.0-cp312-cp312-musllinux_1_2_aarch64.whl",
    ):
        (tmp_path / name).write_bytes(b"")
    target = WheelTarget("cp313", "musllinux", "python:3.13-alpine")
    assert sorted(target.wheels_by_arch(tmp_path)) == ["aarch64", "x86_64"]

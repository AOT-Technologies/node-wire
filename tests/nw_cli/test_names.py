# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for naming helpers."""

from __future__ import annotations

from pathlib import Path

from nw_cli.names import docker_image_tag, mcp_project_dir, server_name


def test_server_name() -> None:
    assert server_name("pet_store") == "pet-store-nw"
    assert server_name("google_drive") == "google-drive-nw"


def test_mcp_project_dir() -> None:
    root = Path("/tmp/nw")
    assert mcp_project_dir(root, "pet_store") == Path(
        "/tmp/nw/nw-mcp-builder/out/pet-store-nw-mcp"
    )


def test_docker_image_tag() -> None:
    assert docker_image_tag("pet_store") == "pet-store-nw-mcp:latest"
    assert docker_image_tag("pet_store", "v1") == "pet-store-nw-mcp:v1"

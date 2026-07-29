#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Naming helpers for generated hosts."""

from __future__ import annotations

from nw_mcp_builder.generate.connector_project import (
    connector_dist_package_name,
    server_name_to_module,
)


def test_server_name_to_module() -> None:
    assert server_name_to_module("google-drive-nw") == "google_drive_nw_mcp"
    assert server_name_to_module("salesforce-nw") == "salesforce_nw_mcp"


def test_connector_dist_package_name() -> None:
    assert connector_dist_package_name("google_drive") == "node-wire-google-drive"
    assert connector_dist_package_name("fhir_epic") == "node-wire-fhir-epic"

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Derive action plans from a resolved OpenAPI 3.x document."""

from __future__ import annotations

from nw_connector_builder.derive.operations import DeriveResult, derive_operations

__all__ = ["DeriveResult", "derive_operations"]

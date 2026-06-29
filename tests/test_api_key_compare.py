#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from node_wire_runtime.caller_identity import api_key_matches


def test_api_key_matches_equal() -> None:
    assert api_key_matches("unit-test-secret", "unit-test-secret") is True


def test_api_key_matches_wrong_value() -> None:
    assert api_key_matches("wrong", "unit-test-secret") is False


def test_api_key_matches_wrong_length() -> None:
    assert api_key_matches("sec", "unit-test-secret") is False

# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""scripts/traffic_petstore.py: its call mix, which runs against the shared public Petstore demo."""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import traffic_petstore as traffic  # noqa: E402

_READ_ONLY = ("pet_store_find_", "pet_store_get_")


def test_the_mix_only_reads_from_the_public_demo() -> None:
    """Writes (add, update, delete, place order, upload) would change a demo others use."""
    calls = [make() for _, make in traffic._mix([14], random.Random(1))]
    real = [c for c in calls if c.kind != "unknown tool"]
    assert real and all(c.tool.startswith(_READ_ONLY) for c in real), [c.tool for c in real]


def test_the_mix_covers_success_upstream_failure_and_rejection() -> None:
    kinds = {make().kind for _, make in traffic._mix([14], random.Random(1))}
    assert kinds == {"ok", "upstream 404", "bad arguments", "unknown tool"}

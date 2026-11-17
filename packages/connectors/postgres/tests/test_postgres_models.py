# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from node_wire_postgres import schema as sch
from node_wire_postgres.logic import PostgresConnector

INPUTS = [
    m
    for m in vars(sch).values()
    if isinstance(m, type) and m.__name__.endswith("Input") and not m.__name__.startswith("_")
]


@pytest.mark.parametrize("model", INPUTS, ids=lambda m: m.__name__)
def test_input_roundtrip(model: type) -> None:
    class _F(ModelFactory):
        __model__ = model

    inst = _F.build()
    again = model.model_validate(inst.model_dump(by_alias=True))
    assert again.action == model.model_fields["action"].default


def test_every_input_has_an_action() -> None:
    declared = {m.model_fields["action"].default for m in INPUTS}
    assert declared == set(PostgresConnector.sdk_action_metas())

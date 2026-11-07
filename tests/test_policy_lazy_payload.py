#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The policy hook gets the same action and payload as the eager model_dump did."""

from __future__ import annotations

from typing import Literal, Optional, Union

import pytest
from pydantic import BaseModel, Field, RootModel

from node_wire_runtime.base_connector import _LazyModelDump, _policy_action_of


class _WithAction(BaseModel):
    action: Literal["send"] = "send"
    to: str = "a@b.c"


class _Other(BaseModel):
    action: Literal["list"] = "list"
    limit: Optional[int] = None


class _NoAction(BaseModel):
    value: int = 1


class _Excluded(BaseModel):
    action: str = Field("hidden", exclude=True)


class _Union(RootModel[Union[_WithAction, _Other]]):
    pass


def _reference(model: BaseModel, default: str) -> str:
    return str(model.model_dump().get("action", default))


@pytest.mark.parametrize(
    "model",
    [
        _WithAction(),
        _NoAction(),
        _Excluded(),
        _Union(_WithAction()),
        _Union(_Other(limit=3)),
        _WithAction.model_construct(action=None),  # type: ignore[arg-type]
    ],
)
def test_action_and_payload_match_eager_dump(model: BaseModel) -> None:
    payload = _LazyModelDump(model)
    assert str(_policy_action_of(model, payload, "fallback")) == _reference(model, "fallback")
    assert dict(payload) == model.model_dump()


def test_payload_not_dumped_unless_read() -> None:
    dumps = 0

    class _Counting(_WithAction):
        def model_dump(self, *a, **k):  # type: ignore[override]
            nonlocal dumps
            dumps += 1
            return super().model_dump(*a, **k)

    m = _Counting()
    payload = _LazyModelDump(m)
    assert _policy_action_of(m, payload, "x") == "send"
    assert dumps == 0
    assert payload["to"] == "a@b.c" and len(payload) == 2
    assert dumps == 1

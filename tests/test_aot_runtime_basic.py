from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action, ConnectorResponse, ErrorCategory, ErrorMapper


class InputModel(BaseModel):
    action: Literal["double"] = "double"
    value: int


class OutputModel(BaseModel):
    doubled: int


class DoubleConnector(BaseConnector):
    connector_id = "test_double"
    output_model = OutputModel

    @nw_action("double")
    async def double(self, params: InputModel, *, trace_id: str) -> OutputModel:
        return OutputModel(doubled=params.value * 2)


def test_successful_execution():
    connector = DoubleConnector()

    response: ConnectorResponse = asyncio.run(connector.run({"action": "double", "value": 2}))

    assert response.success is True
    assert response.data == {"doubled": 4}
    assert response.error_code is None
    assert response.error_category is None
    assert isinstance(response.trace_id, str)


class CustomError(Exception):
    pass


class FailInputModel(BaseModel):
    action: Literal["fail"] = "fail"
    value: int


class FailingConnector(BaseConnector):
    connector_id = "test_fail"
    output_model = OutputModel

    @nw_action("fail")
    async def fail(self, params: FailInputModel, *, trace_id: str) -> OutputModel:
        raise CustomError("boom")


def test_error_mapping_defaults_to_fatal():
    connector = FailingConnector()

    response: ConnectorResponse = asyncio.run(connector.run({"action": "fail", "value": 1}))

    assert response.success is False
    assert response.error_code == "CustomError"
    assert response.error_category == ErrorCategory.FATAL


def test_error_mapping_custom_category():
    ErrorMapper.register(CustomError, ErrorCategory.RETRYABLE, code="CUSTOM_RETRYABLE")
    connector = FailingConnector()

    response: ConnectorResponse = asyncio.run(connector.run({"action": "fail", "value": 1}))

    assert response.success is False
    assert response.error_code == "CUSTOM_RETRYABLE"
    assert response.error_category == ErrorCategory.RETRYABLE

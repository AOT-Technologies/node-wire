from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple, Type, ClassVar
import httpx

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.models import ErrorCategory
from .schema import (
    CreateLeadInput,
    CreateContactInput,
    ReadLeadInput,
    UpdateLeadInput,
    DeleteLeadInput,
    ReadContactInput,
    UpdateContactInput,
    DeleteContactInput,
    SalesforceOperationOutput,
)

logger = logging.getLogger("connectors.salesforce")


class SalesforceTransientError(httpx.HTTPStatusError):
    """Exception for transient Salesforce errors that should be retried."""

    pass


class SalesforceConnector(BaseConnector):
    """Salesforce connector for managing Leads and Contacts."""

    connector_id = "salesforce"
    action = "execute"  # Multi-action dispatcher
    output_model = SalesforceOperationOutput

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "SALESFORCE_CONNECT_ERROR"),
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "SALESFORCE_TIMEOUT"),
        SalesforceTransientError: (ErrorCategory.RETRYABLE, "SALESFORCE_TRANSIENT_ERROR"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "SALESFORCE_API_ERROR"),
    }

    def _get_base_url(self) -> str:
        return self.secret_provider.get_secret("salesforce_instance_url").rstrip("/")

    def _get_api_version(self) -> str:
        return "v58.0"

    async def _get_auth_headers(self) -> Dict[str, str]:
        return await self.get_auth_headers()

    @nw_action("create_lead")
    async def create_lead(
        self, params: CreateLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest(
            "POST", "Lead", params.model_dump(by_alias=True, exclude={"action"}), trace_id
        )

    @nw_action("read_lead")
    async def read_lead(self, params: ReadLeadInput, *, trace_id: str) -> SalesforceOperationOutput:
        return await self._execute_rest("GET", f"Lead/{params.record_id}", None, trace_id)

    @nw_action("update_lead")
    async def update_lead(
        self, params: UpdateLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest(
            "PATCH", f"Lead/{params.record_id}", params.fields, trace_id
        )

    @nw_action("delete_lead")
    async def delete_lead(
        self, params: DeleteLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest("DELETE", f"Lead/{params.record_id}", None, trace_id)

    @nw_action("create_contact")
    async def create_contact(
        self, params: CreateContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest(
            "POST", "Contact", params.model_dump(by_alias=True, exclude={"action"}), trace_id
        )

    @nw_action("read_contact")
    async def read_contact(
        self, params: ReadContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest("GET", f"Contact/{params.record_id}", None, trace_id)

    @nw_action("update_contact")
    async def update_contact(
        self, params: UpdateContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest(
            "PATCH", f"Contact/{params.record_id}", params.fields, trace_id
        )

    @nw_action("delete_contact")
    async def delete_contact(
        self, params: DeleteContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._execute_rest("DELETE", f"Contact/{params.record_id}", None, trace_id)

    async def _execute_rest(
        self, method: str, path: str, payload: Optional[Dict[str, Any]], trace_id: str
    ) -> SalesforceOperationOutput:
        base_url = self._get_base_url()
        api_version = self._get_api_version()
        url = f"{base_url}/services/data/{api_version}/sobjects/{path}"

        headers = await self._get_auth_headers()
        if payload:
            headers["Content-Type"] = "application/json"
            if isinstance(payload, dict):
                payload = {k: v for k, v in payload.items() if v is not None}

        logger.info(
            "Executing Salesforce REST call",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "method": method,
                "path": path,
            },
        )

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method, url, headers=headers, json=payload, timeout=30.0
                )

                # Handle transient errors (5xx) by raising a retryable exception
                if response.status_code >= 500:
                    raise SalesforceTransientError(
                        message=f"Salesforce server error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )

                response.raise_for_status()

                data = {}
                if response.content:
                    try:
                        data = response.json()
                    except Exception:
                        data = {"text": response.text}

                obj_type = path.split("/")[0]
                res_id = data.get("id") or data.get("Id") if isinstance(data, dict) else None

                if not res_id and "/" in path:
                    res_id = path.split("/")[1]

                return SalesforceOperationOutput(
                    success=True, resource_id=res_id, resource_type=obj_type, data=data
                )
            except Exception as exc:
                # We log and re-raise to let the platform (ErrorMapper + Resilience) handle it
                logger.error(
                    "Salesforce REST call failed",
                    extra={
                        "trace_id": trace_id,
                        "connector_id": self.connector_id,
                        "method": method,
                        "path": path,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                raise

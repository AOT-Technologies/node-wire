#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from typing import Any, Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class ChargeInput(BaseModel):
    action: Literal["charge"] = "charge"
    amount: Annotated[int, Field(ge=1, le=99_999_999)]
    currency: Annotated[str, Field(pattern=r"^[a-z]{3}$")]
    source: str
    customer_id: str | None = None
    description: str | None = None
    metadata: dict | None = None
    idempotency_key: str | None = Field(
        None, description="Optional unique key to prevent duplicate operations."
    )

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class ChargeOutput(BaseModel):
    charge_id: str
    receipt_url: str | None = None


class CancelSubscriptionInput(BaseModel):
    action: Literal["cancel_subscription"] = "cancel_subscription"
    subscription_id: str
    cancel_at_period_end: bool = False
    idempotency_key: str | None = Field(
        None, description="Optional unique key to prevent duplicate operations."
    )


class CancelSubscriptionOutput(BaseModel):
    subscription_id: str
    status: str


class CreatePaymentIntentInput(BaseModel):
    action: Literal["create_payment_intent"] = "create_payment_intent"
    amount: Annotated[int, Field(ge=1, le=99_999_999)]
    currency: Annotated[str, Field(pattern=r"^[a-z]{3}$")]
    customer_id: str | None = None
    payment_method: str | None = None
    confirm: bool = False
    description: str | None = None
    metadata: dict | None = None
    idempotency_key: str | None = Field(
        None, description="Optional unique key to prevent duplicate operations."
    )

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class CreatePaymentIntentOutput(BaseModel):
    payment_intent_id: str
    client_secret: str | None = None
    status: str


class CreateSubscriptionInput(BaseModel):
    action: Literal["create_subscription"] = "create_subscription"
    customer_id: str
    price_id: str
    payment_behavior: str = "default_incomplete"
    default_payment_method: str | None = None
    card_token: str | None = None
    metadata: dict | None = None
    idempotency_key: str | None = Field(
        None, description="Optional unique key to prevent duplicate operations."
    )


class CreateSubscriptionOutput(BaseModel):
    subscription_id: str
    client_secret: str | None = None
    status: str


class IssueRefundInput(BaseModel):
    action: Literal["issue_refund"] = "issue_refund"
    charge_id: str | None = None
    payment_intent_id: str | None = None
    amount: int | None = Field(None, ge=1, le=99999999)
    reason: str | None = None
    metadata: dict | None = None
    idempotency_key: str | None = Field(
        None, description="Optional unique key to prevent duplicate operations."
    )


class IssueRefundOutput(BaseModel):
    refund_id: str
    status: str


class StripeOperationOutput(BaseModel):
    """
    Unified output model for all Stripe actions.
    The actual result will be contained in one or more of these fields.
    """

    charge_id: str | None = None
    receipt_url: str | None = None
    subscription_id: str | None = None
    status: str | None = None
    payment_intent_id: str | None = None
    client_secret: str | None = None
    refund_id: str | None = None
    raw: dict[str, Any] | None = None

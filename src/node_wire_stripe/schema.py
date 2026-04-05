from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ChargeInput(BaseModel):
    action: Literal["charge"] = "charge"
    amount: int
    currency: str
    source: str
    description: str | None = None


class ChargeOutput(BaseModel):
    charge_id: str
    receipt_url: str | None = None

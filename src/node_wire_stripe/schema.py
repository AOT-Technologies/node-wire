from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class ChargeInput(BaseModel):
    action: Literal["charge"] = "charge"
    amount: Annotated[int, Field(ge=1, le=99_999_999)]
    currency: Annotated[str, Field(pattern=r"^[a-z]{3}$")]
    source: str
    description: str | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class ChargeOutput(BaseModel):
    charge_id: str
    receipt_url: str | None = None

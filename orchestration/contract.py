"""Shared HTTP request schemas for the orchestration workers."""

from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

Nonnegative = Annotated[StrictInt, Field(ge=0)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Payload = TypeVar("Payload")


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(Schema):
    max_steps: Nonnegative
    max_fetches: Nonnegative
    max_tokens: Nonnegative


class Claim(Schema):
    claim_id: Text
    text: Text
    source_url: Text


class Verify(Schema):
    claims: Annotated[list[Claim], Field(max_length=100)]


class Research(Schema):
    topic: Text
    k: Annotated[StrictInt, Field(ge=3, le=10)]


class Envelope(Schema, Generic[Payload]):
    request_id: UUID
    run_id: UUID
    time_budget_ms: Annotated[StrictInt, Field(gt=0)]
    budget: Budget
    payload: Payload

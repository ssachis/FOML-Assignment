"""Shared HTTP request schemas for the orchestration workers."""

from typing import Annotated, Generic, Literal, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

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


class Development(Schema):
    id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    title: Annotated[str, StringConstraints(max_length=300)]
    summary: Annotated[str, StringConstraints(max_length=1000)]
    company: Annotated[str, StringConstraints(max_length=200)]
    location: Annotated[str, StringConstraints(max_length=200)]
    sources: list[str]
    claims: list[Claim]


class ResearchResult(Schema):
    developments: Annotated[list[Development], Field(max_length=10)]


class Verdict(Schema):
    claim_id: Text
    verdict: Literal["supported", "unsupported", "unreachable"]
    evidence: Optional[Annotated[str, StringConstraints(min_length=1, max_length=300)]]


class VerifyResult(Schema):
    verdicts: Annotated[list[Verdict], Field(max_length=100)]


class Usage(Schema):
    steps: Nonnegative
    fetches: Nonnegative
    tokens: Nonnegative


class WorkerError(Schema):
    code: Text
    message: Text
    retryable: StrictBool


class WorkerResponse(Schema, Generic[Payload]):
    status: Literal["complete", "partial"]
    usage: Usage
    result: Payload
    error: Optional[WorkerError]

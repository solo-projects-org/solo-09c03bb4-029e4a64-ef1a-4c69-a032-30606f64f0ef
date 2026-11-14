"""Pydantic request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    """A privacy-budgeted count query.

    ``extra='forbid'`` makes any unexpected field an explicit 422 instead of
    silently changing the effective parameters, which matters because a
    request id is bound to its exact parameter set.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=128)
    region: str = Field(..., min_length=1, max_length=128)
    outcome: Literal[0, 1]
    epsilon: float = Field(..., gt=0, le=1)


class QueryResponse(BaseModel):
    request_id: str
    region: str
    outcome: Literal[0, 1]
    epsilon: float
    noisy_result: float
    remaining_budget: float
    replayed: bool


class BudgetResponse(BaseModel):
    total_budget: float
    remaining_budget: float
    spent_budget: float


class ImportResponse(BaseModel):
    file_name: str
    imported_rows: int
    distinct_regions: int


class StatusResponse(BaseModel):
    imported: bool
    imported_rows: int
    total_budget: float
    remaining_budget: float
    spent_budget: float

"""Human review decision contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReviewDecision(BaseModel):
    comparison_id: int
    decision: Literal["approve", "reject"]
    note: str | None = None
    reviewer: str | None = None

    model_config = ConfigDict(extra="ignore")
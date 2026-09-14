"""Matching / comparison result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ComparisonStatus = Literal["match", "mismatch", "missing", "converted"]


class FieldComparison(BaseModel):
    """Evidence for a single compared field.

    ``fx`` is populated only for fields that were compared across
    currencies: it records the exchange rate, its date and its source so
    the conversion is fully auditable.
    """

    field: str
    po_value: object | None = None
    invoice_value: object | None = None
    difference: object | None = None
    status: str = "match"
    reason: str | None = None
    fx: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


class MatchResult(BaseModel):
    """Deterministic matching outcome."""

    status: Literal["MATCH", "EXCEPTION"] = "EXCEPTION"
    confidence: str = "LOW"
    reasons: list[str] = []
    field_comparisons: list[FieldComparison] = []
    exception_count: int = 0

    model_config = ConfigDict(extra="ignore")
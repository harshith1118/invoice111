"""Pydantic v2 domain models for InvoiceMatch AI OS.

All monetary values use ``Decimal`` to avoid floating-point drift. Helper
``dump_model`` serializes a model to plain JSON-safe dicts where Decimal
values become strings (so the API and the SQLite JSON blobs stay precise).
"""

from __future__ import annotations

from typing import Any

from .documents import LineItem, PurchaseOrder, Invoice  # noqa: F401
from .matching import FieldComparison, MatchResult  # noqa: F401
from .reviews import ReviewDecision  # noqa: F401

DOCUMENT_TYPE_VALUES = ("purchase_order", "invoice")
RESULT_VALUES = ("MATCH", "EXCEPTION", "INVALID DOCUMENT")
REVIEW_VALUES = ("approve", "reject")
CONFIDENCE_VALUES = ("HIGH", "MEDIUM", "LOW")


def dump_model(model: Any) -> dict[str, Any]:
    """Convert a Pydantic model to a JSON-safe dict with Decimal as strings."""
    return walk_json(model.model_dump(mode="python"))


def walk_json(value: Any) -> Any:
    """Recursively convert a value tree to JSON-safe types (Decimal -> str)."""
    from decimal import Decimal
    from enum import Enum

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): walk_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [walk_json(v) for v in value]
    return value


__all__ = [
    "LineItem",
    "PurchaseOrder",
    "Invoice",
    "FieldComparison",
    "MatchResult",
    "ReviewDecision",
    "DOCUMENT_TYPE_VALUES",
    "RESULT_VALUES",
    "REVIEW_VALUES",
    "CONFIDENCE_VALUES",
    "dump_model",
    "walk_json",
]
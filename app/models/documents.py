"""Document data contracts (PO / Invoice) - strict Pydantic models.

Schema follows the PRD. Every optional financial/document field defaults to
``None`` instead of being invented by extraction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LineItem(BaseModel):
    """A single line item on a PO or invoice."""

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total_price: Decimal | None = None

    model_config = ConfigDict(extra="ignore")


def _require_str_field(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class PurchaseOrder(BaseModel):
    document_type: Literal["purchase_order", "invoice"] = "purchase_order"
    po_number: str | None = None
    vendor_name: str | None = None
    currency: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None

    model_config = ConfigDict(extra="ignore")

    def clean(self) -> "PurchaseOrder":
        """Trim string fields, drop empty ones."""
        return self.model_copy(
            update={
                "po_number": _require_str_field(self.po_number),
                "vendor_name": _require_str_field(self.vendor_name),
                "currency": _require_str_field(self.currency),
                "items": [
                    LineItem(
                        description=_require_str_field(it.description),
                        quantity=it.quantity,
                        unit_price=it.unit_price,
                        total_price=it.total_price,
                    )
                    for it in self.items
                ],
            }
        )


class Invoice(BaseModel):
    document_type: Literal["purchase_order", "invoice"] = "invoice"
    invoice_number: str | None = None
    po_number: str | None = None
    vendor_name: str | None = None
    currency: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None

    model_config = ConfigDict(extra="ignore")

    def clean(self) -> "Invoice":
        return self.model_copy(
            update={
                "invoice_number": _require_str_field(self.invoice_number),
                "po_number": _require_str_field(self.po_number),
                "vendor_name": _require_str_field(self.vendor_name),
                "currency": _require_str_field(self.currency),
                "items": [
                    LineItem(
                        description=_require_str_field(it.description),
                        quantity=it.quantity,
                        unit_price=it.unit_price,
                        total_price=it.total_price,
                    )
                    for it in self.items
                ],
            }
        )
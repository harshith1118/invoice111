"""Deterministic matching engine.

Compares a validated PurchaseOrder against an Invoice and produces
per-field evidence (po_value, invoice_value, difference, status, reason).

All financial arithmetic uses Decimal.
Only MATCH or EXCEPTION is returned here; INVALID is handled upstream.

Cross-currency documents
------------------------
When the PO and invoice are denominated in different currencies the
matcher converts the *invoice* amounts to the PO currency using the
exchange rate supplied by the orchestrator (Frankfurter).  The conversion
is recorded in the evidence: the currency field gets status ``converted``
and every converted amount field carries an ``fx`` block with the rate,
its date and its source.  Quantities are never converted.

If the currencies differ but no rate is available the currency field is
flagged as a mismatch ('exchange rate unavailable') and amount fields are
skipped rather than compared across different units.  Same-currency
documents are compared exactly as before and ignore any supplied rate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.config import get_settings
from app.models.documents import Invoice, LineItem, PurchaseOrder
from app.models.matching import FieldComparison, MatchResult
from app.services.frankfurter import FxRate
from app.services.normalizer import normalize_text, parse_number


# -------------------------------------------------------------------------- helpers
def currencies_differ(po: PurchaseOrder, invoice: Invoice) -> bool:
    """True when the two documents use different (non-empty) currencies."""
    po_cur = normalize_text(po.currency)
    inv_cur = normalize_text(invoice.currency)
    return bool(po_cur and inv_cur and po_cur != inv_cur)


def _dec_str(v: Decimal | None) -> str | None:
    if v is None:
        return None
    return str(v)


def _fx_evidence(fx: FxRate) -> dict[str, Any]:
    """Auditable record of the conversion applied to invoice amounts."""
    return {
        "from_currency": fx.from_currency,
        "to_currency": fx.to_currency,
        "rate": str(fx.rate),
        "date": fx.date,
        "source": fx.source,
        "applied_to": "invoice",
    }


def _compare_strings(
    label: str,
    po_val: str | None,
    inv_val: str | None,
) -> FieldComparison:
    n_po = normalize_text(po_val)
    n_inv = normalize_text(inv_val)
    if n_po is None and n_inv is None:
        return FieldComparison(
            field=label,
            po_value="(missing)",
            invoice_value="(missing)",
            difference=None,
            status="match",
            reason=f"{label}: both values absent (consistent)",
        )
    if n_po is None or n_inv is None:
        status = "mismatch"
        reason = f"{label}: PO value {_q(po_val)} vs Invoice value {_q(inv_val)}"
        return FieldComparison(
            field=label,
            po_value=po_val or "(missing)",
            invoice_value=inv_val or "(missing)",
            difference=None,
            status=status,
            reason=reason,
        )
    if n_po == n_inv:
        return FieldComparison(
            field=label, po_value=po_val, invoice_value=inv_val,
            difference="0", status="match",
        )
    return FieldComparison(
        field=label, po_value=po_val, invoice_value=inv_val,
        difference=f"differs by normalised: '{n_po}' vs '{n_inv}'",
        status="mismatch",
        reason=f"{label} mismatch: PO={_q(po_val)} vs Invoice={_q(inv_val)}",
    )


def _compare_amounts(
    label: str,
    po_val: Decimal | None,
    inv_val: Decimal | None,
    tolerance: Decimal,
) -> FieldComparison:
    if po_val is None and inv_val is None:
        return FieldComparison(
            field=label,
            po_value="(missing)",
            invoice_value="(missing)",
            difference=None,
            status="match",
            reason=f"{label}: both values absent (consistent)",
        )
    if po_val is None or inv_val is None:
        missing_side = "PO" if po_val is None else "Invoice"
        return FieldComparison(
            field=label,
            po_value=_dec_str(po_val) or "(missing)",
            invoice_value=_dec_str(inv_val) or "(missing)",
            difference=None,
            status="missing",
            reason=f"{label}: {missing_side} value missing",
        )
    diff = abs(po_val - inv_val)
    if diff <= tolerance:
        return FieldComparison(
            field=label, po_value=str(po_val), invoice_value=str(inv_val),
            difference=str(diff), status="match",
        )
    return FieldComparison(
        field=label, po_value=str(po_val), invoice_value=str(inv_val),
        difference=str(diff),
        status="mismatch",
        reason=f"{label} mismatch: PO={po_val} vs Invoice={inv_val} (diff {diff})",
    )


def _compare_amount_money(
    label: str,
    po_val: Decimal | None,
    inv_val: Decimal | None,
    tolerance: Decimal,
    fx: FxRate | None,
) -> FieldComparison:
    """Compare a money field, converting the invoice side when FX applies.

    When ``fx`` is set the invoice amount (denominated in
    ``fx.from_currency``) is multiplied by ``fx.rate`` to obtain its value
    in the PO currency (``fx.to_currency``).  Missing values are handled
    exactly like a same-currency comparison.
    """
    if fx is None:
        return _compare_amounts(label, po_val, inv_val, tolerance)
    if po_val is None and inv_val is None:
        return _compare_amounts(label, po_val, inv_val, tolerance)
    if po_val is None or inv_val is None:
        missing_side = "PO" if po_val is None else "Invoice"
        return FieldComparison(
            field=label,
            po_value=_dec_str(po_val) or "(missing)",
            invoice_value=_dec_str(inv_val) or "(missing)",
            difference=None,
            status="missing",
            reason=f"{label}: {missing_side} value missing",
        )
    converted = (inv_val * fx.rate).quantize(Decimal("0.01"))
    diff = abs(po_val - converted)
    invoice_display = f"{inv_val} ({converted})"
    if diff <= tolerance:
        return FieldComparison(
            field=label,
            po_value=str(po_val),
            invoice_value=invoice_display,
            difference=str(diff),
            status="match",
            fx=_fx_evidence(fx),
            reason=(
                f"{label}: converted invoice {inv_val} {fx.from_currency} -> "
                f"{converted} {fx.to_currency} at rate {fx.rate} "
                f"({fx.source}, {fx.date})"
            ),
        )
    return FieldComparison(
        field=label,
        po_value=str(po_val),
        invoice_value=invoice_display,
        difference=str(diff),
        status="mismatch",
        fx=_fx_evidence(fx),
        reason=(
            f"{label}: mismatch after conversion PO={po_val} vs converted "
            f"invoice={converted} (diff {diff})"
        ),
    )


def _compare_currency(
    po_currency: str | None,
    inv_currency: str | None,
    fx: FxRate | None,
    fx_unavailable: bool,
) -> FieldComparison:
    """Compare the currency header, reflecting cross-currency conversion."""
    if fx is None and not fx_unavailable:
        return _compare_strings("currency", po_currency, inv_currency)
    if fx is None:
        return FieldComparison(
            field="currency",
            po_value=po_currency or "(missing)",
            invoice_value=inv_currency or "(missing)",
            difference=None,
            status="mismatch",
            reason=(
                f"currencies differ ({_q(po_currency)} vs {_q(inv_currency)}) "
                "and exchange rate unavailable; amount fields not compared"
            ),
        )
    return FieldComparison(
        field="currency",
        po_value=po_currency,
        invoice_value=inv_currency,
        difference=str(fx.rate),
        status="converted",
        fx=_fx_evidence(fx),
        reason=(
            f"currencies differ ({inv_currency} vs {po_currency}); invoice "
            f"converted at rate {fx.rate} ({fx.source}, {fx.date})"
        ),
    )


def _q(v: object) -> str:
    return repr(v)


# -------------------------------------------------------------------------- line items
def _pair_items(
    po_items: list[LineItem],
    inv_items: list[LineItem],
) -> list[tuple[LineItem | None, LineItem | None]]:
    """Best-effort greedy matching on normalised description.

    Returns a list of (PO item, Invoice item) pairs.  If no 1:1 match is
    possible the result may be shorter or leave items unmatched (paired to
    None).  The caller flags exceptions for any unmatched items.
    """
    inv_unused = list(range(len(inv_items)))
    pairs: list[tuple[LineItem, LineItem | None]] = []
    for po_it in po_items:
        best_idx = None
        best_score = -1
        po_desc = normalize_text(po_it.description)
        for idx in inv_unused:
            it = inv_items[idx]
            if normalize_text(it.description) == po_desc:
                best_idx = idx
                best_score = 2
                break
        pairs.append((po_it, inv_items[best_idx] if best_idx is not None else None))
        if best_idx is not None:
            inv_unused.remove(best_idx)
    # invoice items not found in PO
    pairs.extend((None, inv_items[idx]) for idx in inv_unused)
    return pairs


def _compare_item_pair(
    idx: int,
    po_item: LineItem | None,
    inv_item: LineItem | None,
    amt_tol: Decimal,
    qty_tol: Decimal,
    fx: FxRate | None,
    fx_unavailable: bool,
) -> list[FieldComparison]:
    prefix = f"item_{idx}"
    fields: list[FieldComparison] = []

    # description mapping
    if po_item is None or inv_item is None:
        missing_side = "PO" if po_item is None else "Invoice"
        fields.append(
            FieldComparison(
                field=prefix,
                po_value=po_item.description if po_item else "(missing)",
                invoice_value=inv_item.description if inv_item else "(missing)",
                difference=None,
                status="mismatch",
                reason=f"{prefix}: item exists in {missing_side} but not the other",
            )
        )
        return fields

    # quantity (never converted across currencies)
    fields.append(_compare_amounts(
        f"{prefix}.quantity",
        parse_number(po_item.quantity),
        parse_number(inv_item.quantity),
        qty_tol,
    ))
    if fx_unavailable:
        # cannot compare money across different units without a rate
        return fields
    # unit_price
    fields.append(_compare_amount_money(
        f"{prefix}.unit_price",
        parse_number(po_item.unit_price),
        parse_number(inv_item.unit_price),
        amt_tol,
        fx,
    ))
    # total_price
    fields.append(_compare_amount_money(
        f"{prefix}.total_price",
        parse_number(po_item.total_price),
        parse_number(inv_item.total_price),
        amt_tol,
        fx,
    ))
    return fields


# -------------------------------------------------------------------------- main compare
def compare(
    po: PurchaseOrder,
    invoice: Invoice,
    fx_rate: FxRate | None = None,
) -> MatchResult:
    """Run deterministic field-by-field comparison.

    ``fx_rate`` (``1 invoice_currency = fx_rate.rate po_currency``) is
    applied only when the documents use different currencies; otherwise it
    is ignored and the comparison is purely same-currency.
    """
    settings = get_settings()
    amt_tol = settings.amount_tolerance
    qty_tol = settings.quantity_tolerance

    comparisons: list[FieldComparison] = []
    reasons: list[str] = []

    # --- currency / FX resolution --------------------------------------------
    differ = currencies_differ(po, invoice)
    fx: FxRate | None = fx_rate if differ and fx_rate is not None else None
    fx_unavailable = differ and fx_rate is None

    # --- header fields -------------------------------------------------------
    for cmp in (
        _compare_strings("vendor", po.vendor_name, invoice.vendor_name),
        _compare_strings("po_number", po.po_number, invoice.po_number),
    ):
        comparisons.append(cmp)
        if cmp.reason:
            reasons.append(cmp.reason)
    currency_cmp = _compare_currency(po.currency, invoice.currency, fx, fx_unavailable)
    comparisons.append(currency_cmp)
    if currency_cmp.reason:
        reasons.append(currency_cmp.reason)

    # --- totals --------------------------------------------------------------
    if not fx_unavailable:
        for label, po_v, inv_v in (
            ("subtotal", po.subtotal, invoice.subtotal),
            ("tax", po.tax, invoice.tax),
            ("total", po.total, invoice.total),
        ):
            cmp = _compare_amount_money(label, po_v, inv_v, amt_tol, fx)
            comparisons.append(cmp)
            if cmp.reason:
                reasons.append(cmp.reason)
    # when a rate is unavailable the currency row already explains that the
    # amount fields were not compared

    # --- line items (paired) -------------------------------------------------
    pairs = _pair_items(po.items, invoice.items)
    for idx, (po_it, inv_it) in enumerate(pairs, start=1):
        item_cmps = _compare_item_pair(
            idx, po_it, inv_it, amt_tol, qty_tol, fx, fx_unavailable
        )
        comparisons.extend(item_cmps)
        reasons.extend(c.reason for c in item_cmps if c.reason)

    # item count check
    if len(po.items) != len(invoice.items):
        reason_msg = f"Line-item count differs: PO has {len(po.items)}, invoice has {len(invoice.items)}"
        # only add once; may already be covered by the per-item mismatch above
        if not any(f"Line-item count" in r for r in reasons):
            reasons.insert(0, reason_msg)

    # --- determine overall status ----------------------------------------
    mismatched = [c for c in comparisons if c.status in ("mismatch", "missing")]
    exception_count = len(mismatched)

    if exception_count == 0:
        status = "MATCH"
        confidence = "HIGH"
    else:
        status = "EXCEPTION"
        confidence = "LOW" if exception_count > 2 else "MEDIUM"

    return MatchResult(
        status=status,
        confidence=confidence,
        reasons=reasons,
        field_comparisons=comparisons,
        exception_count=exception_count,
    )
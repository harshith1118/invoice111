"""Tests for app.services.matcher — deterministic matching engine."""

from decimal import Decimal

import pytest

from app.models.documents import Invoice, LineItem, PurchaseOrder
from app.services.matcher import compare


def _po(**kwargs):
    return PurchaseOrder(
        po_number=kwargs.get("po_number", "PO-1001"),
        vendor_name=kwargs.get("vendor_name", "Acme Supplies"),
        currency=kwargs.get("currency", "USD"),
        items=kwargs.get("items", []),
        subtotal=kwargs.get("subtotal"),
        tax=kwargs.get("tax"),
        total=kwargs.get("total"),
    ).clean()


def _inv(**kwargs):
    return Invoice(
        po_number=kwargs.get("po_number", "PO-1001"),
        invoice_number=kwargs.get("invoice_number", "INV-1001"),
        vendor_name=kwargs.get("vendor_name", "Acme Supplies"),
        currency=kwargs.get("currency", "USD"),
        items=kwargs.get("items", []),
        subtotal=kwargs.get("subtotal"),
        tax=kwargs.get("tax"),
        total=kwargs.get("total"),
    ).clean()


def _item(desc="Widget", qty="2", unit="100.00", total="200.00"):
    return LineItem(description=desc, quantity=qty, unit_price=unit, total_price=total)


class TestMatcherPerfectMatch:

    def test_minimal_match(self):
        po = _po(subtotal="2000", tax="360", total="2360")
        inv = _inv(subtotal="2000", tax="360", total="2360")
        r = compare(po, inv)
        assert r.status == "MATCH"
        assert r.exception_count == 0

    def test_single_item_match(self):
        it = _item()
        po = _po(items=[it], subtotal="200", tax="18", total="218")
        inv = _inv(items=[it], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "MATCH"

    def test_multiple_items_match(self):
        items = [_item("A", "1", "10.00", "10.00"), _item("B", "3", "5.00", "15.00")]
        po = _po(items=items, subtotal="25", tax="2.50", total="27.50")
        inv = _inv(items=list(items), subtotal="25", tax="2.50", total="27.50")
        r = compare(po, inv)
        assert r.status == "MATCH"
        assert r.exception_count == 0

    def test_missing_items_both_empty_match(self):
        po = _po(items=[], subtotal="0", tax="0", total="0")
        inv = _inv(items=[], subtotal="0", tax="0", total="0")
        r = compare(po, inv)
        assert r.status == "MATCH"


class TestMatcherQuantityMismatch:

    def test_quantity_mismatch(self):
        po = _po(items=[_item(qty="2")], subtotal="200", tax="18", total="218")
        inv = _inv(items=[_item(qty="3")], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any("quantity" in fc.field.lower() for fc in r.field_comparisons if fc.status == "mismatch")

    def test_quantity_within_tolerance(self):
        from app.config import reload_settings
        import os
        os.environ["QUANTITY_TOLERANCE"] = "1"
        reload_settings()
        po = _po(items=[_item(qty="2")], subtotal="200", tax="18", total="218")
        inv = _inv(items=[_item(qty="3")], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "MATCH"


class TestMatcherPriceMismatch:

    def test_unit_price_mismatch(self):
        po = _po(items=[_item(unit="100.00")], subtotal="200", tax="18", total="218")
        inv = _inv(items=[_item(unit="110.00")], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any("unit_price" in fc.field.lower() for fc in r.field_comparisons if fc.status == "mismatch")

    def test_total_mismatch(self):
        po = _po(subtotal="200", tax="18", total="218")
        inv = _inv(subtotal="200", tax="18", total="225")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any(fc.field == "total" and fc.status == "mismatch" for fc in r.field_comparisons)

    def test_tax_mismatch(self):
        po = _po(subtotal="200", tax="18", total="218")
        inv = _inv(subtotal="200", tax="20", total="220")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any(fc.field == "tax" and fc.status == "mismatch" for fc in r.field_comparisons)


class TestMatcherVendorMismatch:

    def test_vendor_mismatch(self):
        po = _po(vendor_name="Acme")
        inv = _inv(vendor_name="Beta")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any(fc.field == "vendor" and fc.status == "mismatch" for fc in r.field_comparisons)


class TestMatcherPoMismatch:

    def test_po_number_mismatch(self):
        po = _po(po_number="PO-1001")
        inv = _inv(po_number="PO-1002")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert any(fc.field == "po_number" and fc.status == "mismatch" for fc in r.field_comparisons)


class TestMatcherMissingData:

    def test_missing_po_number_on_invoice(self):
        po = _po(po_number="PO-1001")
        inv = _inv(po_number=None)
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        fc = next(f for f in r.field_comparisons if f.field == "po_number")
        assert fc.status in ("mismatch", "missing")

    def test_missing_vendor_on_invoice(self):
        po = _po(vendor_name="Acme")
        inv = _inv(vendor_name=None)
        r = compare(po, inv)
        assert r.status == "EXCEPTION"


class TestMatcherMultipleItems:

    def test_extra_po_item(self):
        po = _po(items=[_item("A"), _item("B")], subtotal="300", tax="27", total="327")
        inv = _inv(items=[_item("A")], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"
        assert r.exception_count >= 2  # item count + at least the extra item

    def test_item_not_found_on_invoice(self):
        po = _po(items=[_item("Laptop")], subtotal="200", tax="18", total="218")
        inv = _inv(items=[_item("Keyboard")], subtotal="200", tax="18", total="218")
        r = compare(po, inv)
        assert r.status == "EXCEPTION"


class TestMatcherTolerance:

    def test_small_amount_difference_within_tolerance(self):
        po = _po(subtotal="2000", tax="360", total="2360")
        inv = _inv(subtotal="2000.005", tax="360", total="2360.005")
        r = compare(po, inv)
        assert r.status == "MATCH"

    def test_amount_exactly_at_boundary(self):
        from decimal import Decimal
        from app.config import reload_settings
        import os
        os.environ["AMOUNT_TOLERANCE"] = "0.01"
        reload_settings()
        po = _po(total="100.00")
        inv = _inv(total="100.01")
        r = compare(po, inv)
        assert r.status == "MATCH"
        po2 = _po(total="100.00")
        inv2 = _inv(total="100.02")
        r2 = compare(po2, inv2)
        assert r2.status == "EXCEPTION"


class TestMatcherFormattingVariation:

    def test_currency_case_folded(self):
        po = _po(currency="USD")
        inv = _inv(currency="USD")
        r = compare(po, inv)
        assert any(fc.field == "currency" and fc.status == "match" for fc in r.field_comparisons)

    def test_vendor_case_insensitive(self):
        po = _po(vendor_name="Acme Supplies")
        inv = _inv(vendor_name="acme supplies")
        r = compare(po, inv)
        assert any(fc.field == "vendor" and fc.status == "match" for fc in r.field_comparisons)


def _fx(rate="1.18", from_cur="USD", to_cur="EUR", date="2026-09-10"):
    from app.services.frankfurter import FxRate
    return FxRate(from_currency=from_cur, to_currency=to_cur,
                  rate=Decimal(rate), date=date)


class TestCrossCurrency:
    """PO/invoice in different currencies, conversion via Frankfurter rate."""

    def test_converted_amounts_match(self):
        # invoice in USD, PO in EUR; 1 USD = 1.18 EUR
        po = _po(currency="EUR", subtotal="2360.00", tax="424.80", total="2784.80")
        inv = _inv(currency="USD", subtotal="2000.00", tax="360.00", total="2360.00")
        r = compare(po, inv, fx_rate=_fx())
        assert r.status == "MATCH"
        assert r.exception_count == 0

    def test_currency_field_is_converted(self):
        po = _po(currency="EUR", subtotal="2360.00", tax="424.80", total="2784.80")
        inv = _inv(currency="USD", subtotal="2000.00", tax="360.00", total="2360.00")
        r = compare(po, inv, fx_rate=_fx())
        fc = next(f for f in r.field_comparisons if f.field == "currency")
        assert fc.status == "converted"
        assert fc.po_value == "EUR"
        assert fc.invoice_value == "USD"
        assert fc.difference == "1.18"

    def test_evidence_records_rate_date_source(self):
        po = _po(currency="EUR", subtotal="2360.00", tax="424.80", total="2784.80")
        inv = _inv(currency="USD", subtotal="2000.00", tax="360.00", total="2360.00")
        r = compare(po, inv, fx_rate=_fx())
        currency_fc = next(f for f in r.field_comparisons if f.field == "currency")
        assert currency_fc.fx == {
            "from_currency": "USD",
            "to_currency": "EUR",
            "rate": "1.18",
            "date": "2026-09-10",
            "source": "frankfurter",
            "applied_to": "invoice",
        }
        # a converted money field carries the same evidence
        total_fc = next(f for f in r.field_comparisons if f.field == "total")
        assert total_fc.fx == currency_fc.fx
        assert total_fc.invoice_value == "2360.00 (2784.80)"

    def test_converted_amount_mismatch(self):
        po = _po(currency="EUR", subtotal="2360.00", tax="424.80", total="2784.80")
        inv = _inv(currency="USD", subtotal="2000.00", tax="360.00", total="2390.00")
        r = compare(po, inv, fx_rate=_fx())
        assert r.status == "EXCEPTION"
        currency_fc = next(f for f in r.field_comparisons if f.field == "currency")
        assert currency_fc.status == "converted"  # conversion itself is fine
        total_fc = next(f for f in r.field_comparisons if f.field == "total")
        assert total_fc.status == "mismatch"
        assert total_fc.fx is not None
        # 2390 USD * 1.18 = 2820.20 EUR vs PO 2784.80 -> diff 35.40
        assert total_fc.difference == "35.40"
        assert total_fc.invoice_value == "2390.00 (2820.20)"

    def test_item_amounts_converted(self):
        po_item = [_item(desc="Laptop", qty="2", unit="1180.00", total="2360.00")]
        inv_item = [_item(desc="Laptop", qty="2", unit="1000.00", total="2000.00")]
        po = _po(currency="EUR", items=po_item,
                 subtotal="2360.00", tax="0.00", total="2360.00")
        inv = _inv(currency="USD", items=inv_item,
                   subtotal="2000.00", tax="0.00", total="2000.00")
        r = compare(po, inv, fx_rate=_fx())
        assert r.status == "MATCH"
        item_fields = {f.field: f for f in r.field_comparisons if f.field.startswith("item_1")}
        assert item_fields["item_1.quantity"].status == "match"
        assert item_fields["item_1.quantity"].fx is None  # quantity is never converted
        assert item_fields["item_1.unit_price"].status == "match"
        assert item_fields["item_1.unit_price"].fx is not None
        assert item_fields["item_1.unit_price"].invoice_value == "1000.00 (1180.00)"
        assert item_fields["item_1.total_price"].status == "match"
        assert item_fields["item_1.total_price"].invoice_value == "2000.00 (2360.00)"

    def test_rate_unavailable_is_exception(self):
        po = _po(currency="EUR", subtotal="100.00", tax="10.00", total="110.00")
        inv = _inv(currency="USD", subtotal="100.00", tax="10.00", total="110.00")
        r = compare(po, inv, fx_rate=None)  # currencies differ, no rate
        assert r.status == "EXCEPTION"
        currency_fc = next(f for f in r.field_comparisons if f.field == "currency")
        assert currency_fc.status == "mismatch"
        assert "exchange rate unavailable" in currency_fc.reason
        # amount fields must NOT be compared across different units
        assert not any(f.field in ("subtotal", "tax", "total") for f in r.field_comparisons)

    def test_rate_unavailable_skips_item_money_but_not_quantity(self):
        po = _po(currency="EUR", items=[_item(qty="2", unit="100.00", total="200.00")],
                 subtotal="200.00", tax="0.00", total="200.00")
        inv = _inv(currency="USD", items=[_item(qty="2", unit="100.00", total="200.00")],
                   subtotal="200.00", tax="0.00", total="200.00")
        r = compare(po, inv, fx_rate=None)
        assert r.status == "EXCEPTION"
        assert any(f.field == "item_1.quantity" for f in r.field_comparisons)
        assert not any(f.field in ("item_1.unit_price", "item_1.total_price")
                       for f in r.field_comparisons)

    def test_same_currency_ignores_fx_rate(self):
        # conversion must never apply when currencies are identical
        po = _po(total="100.00")
        inv = _inv(total="100.00")
        r = compare(po, inv, fx_rate=_fx())
        assert r.status == "MATCH"
        assert not any(f.fx for f in r.field_comparisons)
        currency_fc = next(f for f in r.field_comparisons if f.field == "currency")
        assert currency_fc.status == "match"
        total_fc = next(f for f in r.field_comparisons if f.field == "total")
        assert total_fc.invoice_value == "100.00"  # raw, unconverted
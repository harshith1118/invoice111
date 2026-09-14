"""Generate deterministic synthetic PO/Invoice PDF fixtures with reportlab.

Outputs:
- evaluation/fixtures/  : the 10 reproducible evaluation cases
- sample_data/          : 3 demo pairs for interactive use

All content is fixed (no randomness, no current-date timestamps), so the
byte output is reproducible between runs.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
EVAL_FIXTURES = ROOT / "evaluation" / "fixtures"
SAMPLE_PO = ROOT / "sample_data" / "purchase_orders"
SAMPLE_INV = ROOT / "sample_data" / "invoices"

PAGE_W = 595
PAGE_H = 842
MARGIN_X = 72
MARGIN_Y = 760
LINE_H = 18


def money(v: Decimal | str | int | float) -> str:
    return f"{Decimal(str(v)):.2f}"


def build_po_lines(
    po_number: str,
    vendor: str,
    currency: str,
    items: list[dict],
    subtotal: str,
    tax: str,
    total: str,
    vendor_fmt: str | None = None,
    amount_fmt=None,
) -> list[str]:
    vendor_txt = vendor_fmt or vendor
    subtotal_txt = amount_fmt(subtotal) if amount_fmt else money(subtotal)
    tax_txt = amount_fmt(tax) if amount_fmt else money(tax)
    total_txt = amount_fmt(total) if amount_fmt else money(total)
    return [
        "ACME SUPPLIES",
        "PURCHASE ORDER",
        "",
        f"PO Number: {po_number}",
        f"Vendor: {vendor_txt}",
        f"Currency: {currency}",
        "Date: 2026-09-01",
        "Ship To: Warehouse A",
        "Payment Terms: Net 30",
        "",
        "Items:",
        *[
            f"{i + 1}. {it['description']} | Qty: {it['qty']} | Unit Price: {it['unit']} | Amount: {it['amount']}"
            for i, it in enumerate(items)
        ],
        "",
        f"Subtotal: {subtotal_txt}",
        f"Tax: {tax_txt}",
        f"Total: {total_txt}",
    ]


def build_inv_lines(
    invoice_number: str,
    po_number: str | None,
    vendor: str,
    currency: str,
    items: list[dict],
    subtotal: str,
    tax: str,
    total: str,
    vendor_fmt: str | None = None,
    amount_fmt=None,
    omit_po_number: bool = False,
) -> list[str]:
    vendor_txt = vendor_fmt or vendor
    subtotal_txt = amount_fmt(subtotal) if amount_fmt else money(subtotal)
    tax_txt = amount_fmt(tax) if amount_fmt else money(tax)
    total_txt = amount_fmt(total) if amount_fmt else money(total)
    lines = [
        "ACME SUPPLIES",
        "INVOICE",
        "",
        f"Invoice Number: {invoice_number}",
    ]
    if not omit_po_number:
        lines.append(f"PO Number: {po_number}")
    lines += [
        f"Vendor: {vendor_txt}",
        f"Currency: {currency}",
        "Date: 2026-09-05",
        "Payment Terms: Net 30",
        "",
        "Items:",
        *[
            f"{i + 1}. {it['description']} | Qty: {it['qty']} | Unit Price: {it['unit']} | Amount: {it['amount']}"
            for i, it in enumerate(items)
        ],
        "",
        f"Subtotal: {subtotal_txt}",
        f"Tax: {tax_txt}",
        f"Total: {total_txt}",
    ]
    return lines


def render_pdf(path: Path, lines: list[str], title_hint: str | None = None) -> None:
    """Render text lines as a simple text-based PDF (Helvetica base fonts)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
    y = MARGIN_Y
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN_X, y, title_hint or "")
    y -= LINE_H * 2
    c.setFont("Helvetica", 10)
    for line in lines:
        if y < 60:
            c.showPage()
            y = MARGIN_Y
            c.setFont("Helvetica", 10)
        c.drawString(MARGIN_X, y, line)
        y -= LINE_H
    c.save()


def render_scanned_pdf(path: Path) -> None:
    """Simulate a scanned/image-only PDF (vector graphics, no text layer)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
    c.setFillColorRGB(0.93, 0.93, 0.95)
    c.rect(60, 580, 475, 200, stroke=0, fill=1)
    c.setFillColorRGB(0.8, 0.8, 0.85)
    c.rect(80, 620, 435, 8, stroke=0, fill=1)
    c.rect(80, 650, 300, 8, stroke=0, fill=1)
    c.rect(80, 690, 380, 8, stroke=0, fill=1)
    c.setFillColorRGB(0.5, 0.5, 0.55)
    for i in range(10):
        c.rect(80, 560 - i * 16, 320, 4, stroke=0, fill=1)
    # deliberately no text anywhere
    c.save()


def build_all() -> dict[str, list[Path]]:
    created: dict[str, list[Path]] = {"eval": [], "po": [], "inv": []}

    # ----- evaluation cases -------------------------------------------------
    cases: list[dict] = [
        dict(
            case_id=1, po_number="PO-1001", inv_number="INV-1001",
            vendor="Acme Supplies", currency="USD",
            items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
        ),
        dict(
            # quantity mismatch: qty 3 vs PO qty 2, line/sub totals unchanged
            case_id=2, po_number="PO-1002", inv_number="INV-1002",
            vendor="Acme Supplies", currency="USD",
            po_items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            inv_items=[dict(description="Laptop", qty="3", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
        ),
        dict(
            # unit-price mismatch: 600.00 vs 500.00, amount unchanged
            case_id=3, po_number="PO-1003", inv_number="INV-1003",
            vendor="Acme Supplies", currency="USD",
            po_items=[dict(description="Consulting Services", qty="4", unit="500.00", amount="2000.00")],
            inv_items=[dict(description="Consulting Services", qty="4", unit="600.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
        ),
        dict(
            # total mismatch: grand total typo on invoice
            case_id=4, po_number="PO-1004", inv_number="INV-1004",
            vendor="Acme Supplies", currency="USD",
            items=[dict(description="Server Rack", qty="1", unit="700.00", amount="700.00")],
            subtotal="700.00", tax="63.00", total="763.00",
            inv_total="765.00",
            expected_mismatch=["total"],
        ),
        dict(
            # vendor mismatch
            case_id=5, po_number="PO-1005", inv_number="INV-1005",
            vendor="Acme Supplies", currency="USD",
            inv_vendor="Beta Widgets Inc",
            items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
            expected_mismatch=["vendor_name"],
        ),
        dict(
            # PO-number mismatch on the invoice
            case_id=6, po_number="PO-1006", inv_number="INV-1006",
            vendor="Acme Supplies", currency="USD",
            inv_po_number="PO-1007",
            items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
            expected_mismatch=["po_number"],
        ),
        dict(
            # missing required field: invoice has no PO reference
            case_id=7, po_number="PO-1008", inv_number="INV-1008",
            vendor="Acme Supplies", currency="USD",
            items=[dict(description="Office Chair", qty="4", unit="150.00", amount="600.00")],
            subtotal="600.00", tax="54.00", total="654.00",
            omit_po_number=True,
            expected_mismatch=["po_number"],
        ),
        dict(
            # multiple line items that all match
            case_id=8, po_number="PO-1009", inv_number="INV-1009",
            vendor="Acme Supplies", currency="USD",
            items=[
                dict(description="Office Chair", qty="4", unit="150.00", amount="600.00"),
                dict(description="Standing Desk", qty="2", unit="450.00", amount="900.00"),
                dict(description="Monitor", qty="5", unit="200.00", amount="1000.00"),
            ],
            subtotal="2500.00", tax="225.00", total="2725.00",
        ),
        dict(
            # formatting variation: case/whitespace in vendor, thousand separators
            case_id=9, po_number="PO-1010", inv_number="INV-1010",
            vendor="Acme Supplies", currency="USD",
            vendor_fmt_po="Acme  Supplies",
            vendor_fmt_inv="acme supplies",
            items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
            amount_fmt_po=lambda v: money(v),
            amount_fmt_inv=lambda v: _thousands(money(v)),
        ),
        dict(
            # invalid/unreadable: invoice is a scanned (no-text) document
            case_id=10, po_number="PO-1011", inv_number="INV-1011",
            vendor="Acme Supplies", currency="USD",
            items=[dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
            subtotal="2000.00", tax="360.00", total="2360.00",
            scanned_invoice=True,
        ),
    ]

    for case in cases:
        po_data = dict(case)
        inv_data = dict(case)
        items = case.get("items") or []
        po_items = case.get("po_items") or items
        inv_items = case.get("inv_items") or items

        # PO content
        po_lines = build_po_lines(
            po_number=case["po_number"],
            vendor=case["vendor"],
            currency=case["currency"],
            items=po_items,
            subtotal=case["subtotal"],
            tax=case["tax"],
            total=case["total"],
            vendor_fmt=case.get("vendor_fmt_po"),
            amount_fmt=case.get("amount_fmt_po"),
        )
        po_pdf = EVAL_FIXTURES / f"po_{case['case_id']:02d}.pdf"
        render_pdf(po_pdf, po_lines, title_hint="PURCHASE ORDER")
        created["eval"].append(po_pdf)

        # Invoice content
        inv_total = case.get("inv_total", case["total"])
        if case.get("scanned_invoice"):
            inv_pdf = EVAL_FIXTURES / f"inv_{case['case_id']:02d}.pdf"
            render_scanned_pdf(inv_pdf)
            created["eval"].append(inv_pdf)
            continue

        inv_lines = build_inv_lines(
            invoice_number=case["inv_number"],
            po_number=case.get("inv_po_number", case["po_number"]),
            vendor=case.get("inv_vendor", case["vendor"]),
            currency=case["currency"],
            items=inv_items,
            subtotal=case["subtotal"],
            tax=case["tax"],
            total=inv_total,
            vendor_fmt=case.get("vendor_fmt_inv"),
            amount_fmt=case.get("amount_fmt_inv"),
            omit_po_number=case.get("omit_po_number", False),
        )
        inv_pdf = EVAL_FIXTURES / f"inv_{case['case_id']:02d}.pdf"
        render_pdf(inv_pdf, inv_lines, title_hint="INVOICE")
        created["eval"].append(inv_pdf)

    # ----- sample data (demo pairs) -----------------------------------------
    # 1) perfect match
    po1 = build_po_lines("PO-1001", "Acme Supplies", "USD",
                         [dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
                         "2000.00", "360.00", "2360.00")
    inv1 = build_inv_lines("INV-1001", "PO-1001", "Acme Supplies", "USD",
                           [dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
                           "2000.00", "360.00", "2360.00")
    p1 = SAMPLE_PO / "sample_match_PO-1001.pdf"
    i1 = SAMPLE_INV / "sample_match_INV-1001.pdf"
    render_pdf(p1, po1, "PURCHASE ORDER")
    render_pdf(i1, inv1, "INVOICE")
    created["po"].append(p1); created["inv"].append(i1)

    # 2) quantity exception
    po2 = build_po_lines("PO-1002", "Acme Supplies", "USD",
                         [dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
                         "2000.00", "360.00", "2360.00")
    inv2 = build_inv_lines("INV-1002", "PO-1002", "Acme Supplies", "USD",
                           [dict(description="Laptop", qty="3", unit="1000.00", amount="2000.00")],
                           "2000.00", "360.00", "2360.00")
    p2 = SAMPLE_PO / "sample_qty_exception_PO-1002.pdf"
    i2 = SAMPLE_INV / "sample_qty_exception_INV-1002.pdf"
    render_pdf(p2, po2, "PURCHASE ORDER")
    render_pdf(i2, inv2, "INVOICE")
    created["po"].append(p2); created["inv"].append(i2)

    # 3) invalid: valid PO + scanned invoice
    po3 = build_po_lines("PO-1011", "Acme Supplies", "USD",
                         [dict(description="Laptop", qty="2", unit="1000.00", amount="2000.00")],
                         "2000.00", "360.00", "2360.00")
    p3 = SAMPLE_PO / "sample_invalid_PO-1011.pdf"
    i3 = SAMPLE_INV / "sample_invalid_scanned_invoice.pdf"
    render_pdf(p3, po3, "PURCHASE ORDER")
    render_scanned_pdf(i3)
    created["po"].append(p3); created["inv"].append(i3)

    return created


def _thousands(v: str) -> str:
    """'2000.00' -> '2,000.00' (integer-part thousand separators)."""
    if "." in v:
        integer, frac = v.split(".", 1)
    else:
        integer, frac = v, ""
    out = []
    while len(integer) > 3:
        out.insert(0, integer[-3:])
        integer = integer[:-3]
    out.insert(0, integer)
    return ",".join(out) + (f".{frac}" if frac else "")


def main() -> None:
    created = build_all()
    for group, paths in created.items():
        print(f"{group:5s}: {len(paths)} files")
    for path in [EVAL_FIXTURES, SAMPLE_PO, SAMPLE_INV]:
        print(f"  -> {path}")


if __name__ == "__main__":
    sys.exit(main())
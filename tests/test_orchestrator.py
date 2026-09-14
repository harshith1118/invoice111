"""End-to-end FX wiring tests through the analyze pipeline.

The orchestrator must (a) fetch a Frankfurter rate ONLY when the PO and
invoice use different currencies, (b) feed that rate into the deterministic
matcher, and (c) survive rate-fetch failures as a reviewable EXCEPTION.
The real FrankfurterClient runs here against a stubbed ``urllib`` layer so
the full client code path (URL building, JSON parsing, Decimal rate) is
exercised without touching the public API.
"""

import io
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

from tests.conftest import make_doc_pdf


def _po_lines():
    return [
        "Purchase Order",
        "PO Number: FX-1001",
        "Vendor: FX Vendor",
        "Currency: EUR",
        "Subtotal: 2360.00",
        "Tax: 424.80",
        "Total: 2784.80",
        "1. Laptop | Qty: 2 | Unit Price: 1180.00 | Amount: 2360.00",
    ]


def _inv_lines():
    return [
        "Invoice",
        "Invoice Number: INV-FX-1001",
        "PO Number: FX-1001",
        "Vendor: FX Vendor",
        "Currency: USD",
        "Subtotal: 2000.00",
        "Tax: 360.00",
        "Total: 2360.00",
        "1. Laptop | Qty: 2 | Unit Price: 1000.00 | Amount: 2000.00",
    ]


def _post(client, po_lines=None, inv_lines=None):
    return client.post(
        "/api/analyze",
        files={
            "po": (
                "po_fx.pdf",
                make_doc_pdf(po_lines or _po_lines(), "Purchase Order"),
                "application/pdf",
            ),
            "invoice": (
                "inv_fx.pdf",
                make_doc_pdf(inv_lines or _inv_lines(), "Invoice"),
                "application/pdf",
            ),
        },
    )


def _stub_rate(monkeypatch, rate=1.18, date="2026-09-10"):
    """Answer any Frankfurter call with ``1 base = rate target``."""
    def fake_urlopen(req, timeout=None):
        qs = parse_qs(urlsplit(req.full_url).query)
        base = qs["from"][0]
        target = qs["to"][0]
        payload = {"base": base, "date": date, "rates": {target: rate}}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _stub_rate_failure(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


class TestOrchestratorFx:

    def test_cross_currency_match_with_rate(self, client, monkeypatch):
        _stub_rate(monkeypatch)
        r = _post(client)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "MATCH"

        currency = next(
            f for f in body["field_comparisons"] if f["field"] == "currency"
        )
        assert currency["status"] == "converted"
        assert currency["fx"]["rate"] == "1.18"
        assert currency["fx"]["source"] == "frankfurter"
        assert currency["fx"]["from_currency"] == "USD"
        assert currency["fx"]["to_currency"] == "EUR"
        assert any(f.get("fx") for f in body["field_comparisons"])

        # the evidence and FX stage survive into the persisted detail
        cid = body["comparison_id"]
        detail = client.get(f"/api/comparisons/{cid}").json()
        assert any(f.get("fx") for f in detail["field_comparisons"])
        fx_logs = [e for e in detail["logs"] if e["stage"] == "FX"]
        assert fx_logs and any(e["status"] == "ok" for e in fx_logs)

    def test_cross_currency_amount_mismatch(self, client, monkeypatch):
        _stub_rate(monkeypatch)
        inv_lines = [
            "Invoice",
            "Invoice Number: INV-FX-1001",
            "PO Number: FX-1001",
            "Vendor: FX Vendor",
            "Currency: USD",
            "Subtotal: 2000.00",
            "Tax: 360.00",
            "Total: 2390.00",  # 2390 USD * 1.18 = 2820.20 EUR != 2784.80
            "1. Laptop | Qty: 2 | Unit Price: 1000.00 | Amount: 2000.00",
        ]
        r = _post(client, inv_lines=inv_lines)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "EXCEPTION"
        assert body["exception_count"] >= 1
        total = next(f for f in body["field_comparisons"] if f["field"] == "total")
        assert total["status"] == "mismatch"
        assert total["fx"]["rate"] == "1.18"

    def test_rate_fetch_failure_is_non_fatal(self, client, monkeypatch):
        _stub_rate_failure(monkeypatch)
        r = _post(client)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "EXCEPTION"
        assert "exchange rate unavailable" in " ".join(body["reasons"])

        cid = body["comparison_id"]
        detail = client.get(f"/api/comparisons/{cid}").json()
        fx_logs = [e for e in detail["logs"] if e["stage"] == "FX"]
        assert fx_logs and any(e["status"] == "error" for e in fx_logs)

    def test_same_currency_never_calls_frankfurter(self, client, monkeypatch):
        calls: list[str] = []

        def fake_urlopen(req, timeout=None):
            calls.append("urlopen-called")
            raise AssertionError("no Frankfurter call allowed for same-currency docs")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        po = make_doc_pdf(
            [
                "Purchase Order",
                "PO Number: P-1",
                "Vendor: Acme",
                "Currency: USD",
                "Subtotal: 2000.00",
                "Tax: 360.00",
                "Total: 2360.00",
                "1. Laptop | Qty: 2 | Unit Price: 1000.00 | Amount: 2000.00",
            ],
            "Purchase Order",
        )
        inv = make_doc_pdf(
            [
                "Invoice",
                "Invoice Number: I-1",
                "PO Number: P-1",
                "Vendor: Acme",
                "Currency: USD",
                "Subtotal: 2000.00",
                "Tax: 360.00",
                "Total: 2360.00",
                "1. Laptop | Qty: 2 | Unit Price: 1000.00 | Amount: 2000.00",
            ],
            "Invoice",
        )
        r = client.post(
            "/api/analyze",
            files={
                "po": ("p.pdf", po, "application/pdf"),
                "invoice": ("i.pdf", inv, "application/pdf"),
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "MATCH"
        assert calls == []
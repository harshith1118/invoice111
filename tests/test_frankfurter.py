"""Tests for app.services.frankfurter — Frankfurter exchange-rate client.

The client is the second "always-on" external integration.  It is only
used for documents whose currencies differ, so these tests monkeypatch the
network layer and never hit the public API.
"""

import io
import json
import urllib.error
from decimal import Decimal

import pytest

from app.services.frankfurter import FrankfurterClient, RateFetchError


def _payload(from_cur="EUR", to_cur="USD", rate=1.18, date="2026-09-12", base="EUR"):
    return {"base": base, "date": date, "rates": {to_cur: rate}}


def _json_bytes(data) -> bytes:
    return json.dumps(data).encode()


def _grab_url(monkeypatch):
    """Record the request URL and stub the real urllib urlopen."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return io.BytesIO(_json_bytes(_payload()))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


class TestFrankfurterClient:

    def test_builds_url_and_parses_rate(self, monkeypatch):
        captured = _grab_url(monkeypatch)
        client = FrankfurterClient(base_url="https://example.test/v1", timeout=3)
        fx = client.get_rate("EUR", "USD")

        assert "https://example.test/v1/latest" in captured["url"]
        assert "from=EUR" in captured["url"]
        assert "to=USD" in captured["url"]
        assert fx.from_currency == "EUR"
        assert fx.to_currency == "USD"
        assert fx.rate == Decimal("1.18")
        assert fx.date == "2026-09-12"
        assert fx.source == "frankfurter"

    def test_codes_are_upper_cased(self, monkeypatch):
        captured = _grab_url(monkeypatch)
        client = FrankfurterClient()
        fx = client.get_rate("eur", "usd")
        assert fx.from_currency == "EUR"
        assert fx.to_currency == "USD"
        assert "from=EUR" in captured["url"]

    def test_invalid_currency_code_raises(self):
        client = FrankfurterClient()
        with pytest.raises(RateFetchError, match="Invalid currency code"):
            client.get_rate("US", "EUR")  # two letters only

    def test_symbol_currency_code_raises(self):
        client = FrankfurterClient()
        with pytest.raises(RateFetchError):
            client.get_rate("$", "EUR")

    def test_empty_currency_code_raises(self):
        client = FrankfurterClient()
        with pytest.raises(RateFetchError):
            client.get_rate("", "EUR")

    def test_missing_target_rate_raises(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps({"base": "EUR", "rates": {"GBP": 0.9}}).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(RateFetchError, match="no rate"):
            FrankfurterClient().get_rate("EUR", "USD")

    def test_http_error_raises_rate_fetch_error(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Unsupported code", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", boom)
        with pytest.raises(RateFetchError, match="HTTP 400"):
            FrankfurterClient().get_rate("XYZ", "USD")

    def test_network_error_raises_rate_fetch_error(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        with pytest.raises(RateFetchError, match="unavailable"):
            FrankfurterClient().get_rate("EUR", "USD")

    def test_non_numeric_rate_raises(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            return io.BytesIO(
                json.dumps({"base": "EUR", "rates": {"USD": "NaN-ish"}}).encode()
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(RateFetchError, match="non-numeric"):
            FrankfurterClient().get_rate("EUR", "USD")

    def test_defaults_use_settings(self, monkeypatch):
        import os

        os.environ["FRANKFURTER_BASE_URL"] = "https://override.test/v9"
        from app.config import reload_settings
        reload_settings()

        captured = _grab_url(monkeypatch)
        fx = FrankfurterClient().get_rate("EUR", "USD")
        assert "https://override.test/v9/latest" in captured["url"]
        assert fx.rate == Decimal("1.18")

        os.environ.pop("FRANKFURTER_BASE_URL", None)
        reload_settings()
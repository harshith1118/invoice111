"""Frankfurter exchange-rate client (second external integration).

Frankfurter (https://frankfurter.dev) is a free, open REST API for ECB
reference exchange rates that requires no API key.  Currency conversion is
only *needed* when a PO and its invoice are denominated in different
currencies, so the orchestrator only calls this client for documents whose
normalized currencies differ.  Same-currency documents never touch the
network.

Rate semantics: ``GET {base}/latest?from=X&to=Y`` returns
``{"base": X, "date": "...", "rates": {Y: n}}`` where ``1 X = n Y``.

All conversion arithmetic lives in the matcher; this module only fetches
and validates the rate.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from app.config import get_settings

_ISO_CODE = re.compile(r"^[A-Z]{3}$")


class RateFetchError(Exception):
    """Raised when an exchange rate cannot be fetched."""


@dataclass(frozen=True)
class FxRate:
    """A single exchange-rate snapshot used for conversion.

    Semantics: ``1 from_currency = rate to_currency``.
    """

    from_currency: str
    to_currency: str
    rate: Decimal
    date: str
    source: str = "frankfurter"


class FrankfurterClient:
    """Thin client over the Frankfurter public API (no API key needed)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.frankfurter_base_url).rstrip("/")
        self.timeout = (
            timeout if timeout is not None else settings.frankfurter_timeout_seconds
        )

    @staticmethod
    def _validate_code(code: str) -> str:
        norm = (code or "").strip().upper()
        if not _ISO_CODE.match(norm):
            raise RateFetchError(f"Invalid currency code {code!r}")
        return norm

    def get_rate(self, from_currency: str, to_currency: str) -> FxRate:
        """Fetch ``1 from_currency = rate to_currency`` from Frankfurter.

        Raises :class:`RateFetchError` on any failure (invalid code,
        network error, HTTP error, missing/NaN rate) so the caller can
        degrade gracefully instead of crashing the pipeline.
        """
        src = self._validate_code(from_currency)
        dst = self._validate_code(to_currency)
        params = urllib.parse.urlencode({"from": src, "to": dst})
        url = f"{self.base_url}/latest?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "InvoiceMatch/1.0", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RateFetchError(f"Frankfurter HTTP {exc.code} for {src}->{dst}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RateFetchError(f"Frankfurter unavailable: {reason}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RateFetchError(f"Frankfurter error: {exc}") from exc

        rates = payload.get("rates") or {}
        if dst not in rates:
            raise RateFetchError(f"Frankfurter returned no rate for {src}->{dst}")
        try:
            rate = Decimal(str(rates[dst]))
        except Exception:
            raise RateFetchError(
                f"Frankfurter returned non-numeric rate {rates[dst]!r}"
            ) from None

        return FxRate(
            from_currency=src,
            to_currency=dst,
            rate=rate,
            date=str(payload.get("date") or ""),
        )
"""Conservative text + number normalization for comparisons.

The MVP deliberately avoids semantic matching. Normalization is limited to:
- Unicode NFKC form (normalizes full-width chars, dashes, quotes)
- lowercase
- strip + collapse internal whitespace
- drop simple trailing punctuation like "." / ":"

If normalization cannot confidently make two values comparable, the case
must go to EXCEPTION downstream rather than be fuzzy-matched here.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_WS = re.compile(r"\s+")
_NUM_CLEAN = re.compile(r"[^\d.,()\-]")

# Currency symbols to strip when parsing numbers (safe, common cases).
_CURRENCY_SYMBOLS = ("$", "€", "£", "¥", "₹", "USD ", "EUR ", "GBP ")


def normalize_text(value: str | None, strip_punct: bool = True) -> str | None:
    """Normalize free text conservatively for equality comparisons."""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip().lower()
    s = _WS.sub(" ", s)
    if strip_punct:
        # drop a single trailing dot/colon that often ends table cells
        s = re.sub(r"[.:\s]+$", "", s)
    return s or None


def parse_number(value: str | int | float | Decimal | None) -> Decimal | None:
    """Parse a numeric token tolerantly (commas, currency symbols, parens)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip()
    if not s:
        return None
    # normalize full-width digits
    s = unicodedata.normalize("NFKC", s)
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1].strip()
    for sym in _CURRENCY_SYMBOLS:
        if s.upper().startswith(sym.strip()):
            s = s[len(sym.strip()):].strip()
            break
    s = _NUM_CLEAN.sub("", s)
    if s in ("", "."):
        return None

    # Resolve commas as thousands separators vs decimal comma (conservative).
    if "," in s:
        tail = s.rsplit(",", 1)[1]
        is_thousands = (
            "." in tail  # e.g. 2,000.00
            or (len(tail) == 3 and tail.isdigit())  # e.g. 2,000 / 1,234,567
            or s.count(",") > 1
        )
        s = s.replace(",", "") if is_thousands else s.replace(",", ".")

    if s.count(".") > 1:
        # likely 1.234.567 style thousands; only digits remain after clean
        s = s.replace(".", "")
    try:
        value_out = Decimal(s)
    except InvalidOperation:
        return None
    return -value_out if negative else value_out


def amount_difference(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return abs(a - b)
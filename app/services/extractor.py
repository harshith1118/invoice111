"""Extractor factory.

The production extractor is ``GroqExtractor`` which sends PDF text to the
Groq API for structured extraction.  ``DeterministicExtractor`` exists
exclusively for offline evaluation and tests; it is never used unless
``EXTRACTOR_MODE=deterministic_eval``.

Both extractors return a raw Python dict (suitable for Pydantic validation)
and the extractor mode tag that appeared in the result.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

from app.config import get_settings
from app.services.logger import log_stage

# Allow the rest of the module to import without the groq package at top
# level when the deterministic extractor is active.
groq: Any = None
try:
    import groq as _groq
    groq = _groq
except ImportError:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ExtractionError(Exception):
    """Raised when structured extraction fails (recording an error stage)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Deterministic extractor (evaluation / test only)
# ---------------------------------------------------------------------------
_JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_number(s: str | None) -> Any:
    """Parse a numeric token (Decimal result; walk_json converts to str)."""
    from app.services.normalizer import parse_number as robust_parse
    return robust_parse(s)


class DeterministicExtractor:
    """Pure-regex extractor for known fixture formats.

    Extraction accuracy is measured as part of the evaluation; this
    extractor is *not* intended for production use and must not be silently
    deployed as a fallback.  Its use is always explicitly labeled
    ``"extractor": "deterministic_eval"`` in results.
    """

    MODE: Literal["deterministic_eval"] = "deterministic_eval"

    def extract(self, text: str, doc_hint: str | None = None) -> dict[str, Any]:
        """Heuristic line-oriented extraction from the known fixture format."""
        lines = [l.strip() for l in text.splitlines()]
        result: dict[str, Any] = {}

        # Determine document type from hint or content
        doc_type = "invoice" if doc_hint == "invoice" else "purchase_order"

        result["document_type"] = doc_type

        def _find(r: str) -> str | None:
            pat = re.compile(r"^\s*" + r + r"\s*[:=]\s*(.+)", re.IGNORECASE)
            for line in lines:
                m = pat.match(line)
                if m:
                    # collapse internal whitespace, trim edge padding
                    raw = " ".join(m.group(1).split()).strip()
                    return raw or None
            return None

        result["po_number"] = _find(r"PO\s*(?:Number|#|No\.?)")
        result["vendor_name"] = _find(r"Vendor\s*(?:Name)?")
        result["currency"] = _find(r"Currency") or "USD"
        result["invoice_number"] = _find(r"Invoice\s*(?:Number|#|No\.?)")

        # Totals
        for key, label in [
            ("subtotal", r"Subtotal"),
            ("tax", r"Tax"),
            ("total", r"Grand\s*Total|Total"),
        ]:
            raw = _find(label)
            result[key] = _parse_number(raw)

        # Line items: rows like "1. Widget | Qty: 2 | Unit Price: 10.00 | Amount: 20.00"
        items: list[dict[str, Any]] = []
        item_re = re.compile(
            r"^\s*\d+\.\s+(.+?)\s*\|\s*Qty:\s*([^|]+)\s*\|\s*Unit Price:\s*([^|]+)\s*\|\s*Amount:\s*([^|]+)",
            re.IGNORECASE,
        )
        for line in lines:
            m = item_re.match(line)
            if m:
                items.append({
                    "description": m.group(1).strip(),
                    "quantity": _parse_number(m.group(2)),
                    "unit_price": _parse_number(m.group(3)),
                    "total_price": _parse_number(m.group(4)),
                })
        result["items"] = items

        # Ensure result contains all expected fields even if extraction found nothing
        for field in ("po_number", "vendor_name", "currency", "subtotal", "tax", "total"):
            result.setdefault(field, None)
        result.setdefault("items", [])
        result.setdefault("invoice_number", None)
        return result


# ---------------------------------------------------------------------------
# Groq extractor
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a precise document extraction assistant.\n"
    "You receive the raw text content of a financial document.\n"
    "Your task is to extract structured fields into valid JSON.\n\n"
    "RULES:\n"
    "- Return ONLY valid JSON. No markdown. No explanation.\n"
    "- Return null for fields that are genuinely absent. Never invent values.\n"
    "- The document type is either 'purchase_order' or 'invoice'.\n"
    "- Monetary values must be numeric (not strings).\n"
    "- For items, return a list of objects with description, quantity, unit_price, total_price.\n"
    "- Do not include extra fields not defined in the schema.\n"
)

_PO_SCHEMA = (
    '{\n'
    '  "document_type": "purchase_order",\n'
    '  "po_number": "string|null",\n'
    '  "vendor_name": "string|null",\n'
    '  "currency": "string|null",\n'
    '  "items": [\n'
    '    {"description":"string","quantity":"number|null","unit_price":"number|null","total_price":"number|null"}\n'
    '  ],\n'
    '  "subtotal": "number|null",\n'
    '  "tax": "number|null",\n'
    '  "total": "number|null"\n'
    '}'
)

_INV_SCHEMA = (
    '{\n'
    '  "document_type": "invoice",\n'
    '  "invoice_number": "string|null",\n'
    '  "po_number": "string|null",\n'
    '  "vendor_name": "string|null",\n'
    '  "currency": "string|null",\n'
    '  "items": [\n'
    '    {"description":"string","quantity":"number|null","unit_price":"number|null","total_price":"number|null"}\n'
    '  ],\n'
    '  "subtotal": "number|null",\n'
    '  "tax": "number|null",\n'
    '  "total": "number|null"\n'
    '}'
)

_USER_PROMPT = (
    "Extract structured data from this document text. "
    "Return the JSON matching the correct schema:\n\n"
    "Purchase Order schema:\n{_po}\n\n"
    "Invoice schema:\n{_inv}\n\n"
    "Only include fields relevant to the document type.\n"
    "Use null when a value is not present. Never guess.\n\n"
    "DOCUMENT TEXT:\n---\n{document_text}\n---\n\n"
    "Return only valid JSON."
)


class GroqExtractor:
    """Production extractor that calls the Groq API."""

    MODE: Literal["groq"] = "groq"

    def extract(
        self, text: str, analysis_id: int | None = None
    ) -> dict[str, Any]:
        settings = get_settings()

        if not settings.groq_api_key:
            raise ExtractionError(
                "GROQ_API_KEY is not configured. "
                "Cannot run Groq extraction without an API key."
            )

        if groq is None:
            raise ExtractionError(
                "The groq package is not installed. "
                "Install it with: pip install groq"
            )

        user_prompt = _USER_PROMPT.replace("{_po}", _PO_SCHEMA).replace(
            "{_inv}", _INV_SCHEMA
        ).replace("{document_text}", text)

        last_exc = None
        for attempt in range(1, settings.groq_max_retries + 1):
            try:
                client = groq.Groq(
                    api_key=settings.groq_api_key,
                    timeout=settings.groq_timeout_seconds,
                )
                response = client.chat.completions.create(
                    model=settings.groq_model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content.strip()
                # Strip markdown code fences if the model wraps output anyway
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                data = json.loads(raw)
                log_stage(
                    analysis_id,
                    "GROQ_EXTRACTION",
                    "ok",
                    f"attempt={attempt}",
                )
                return data

            except json.JSONDecodeError as exc:
                last_exc = exc
                log_stage(
                    analysis_id,
                    "GROQ_EXTRACTION",
                    "error",
                    f"Invalid JSON (attempt {attempt}): {exc}",
                )
            except (groq.RateLimitError, groq.APIStatusError, groq.APIConnectionError, TimeoutError) as exc:
                last_exc = exc
                retryable = isinstance(exc, (groq.RateLimitError, groq.APIConnectionError))
                log_stage(
                    analysis_id,
                    "GROQ_EXTRACTION",
                    "error",
                    f"API error (attempt {attempt}): {type(exc).__name__}: {exc} (retryable={retryable})",
                )
                if not retryable and attempt < settings.groq_max_retries:
                    break  # don't retry non-transient errors

            # Exponential-ish back-off (0.5s, 1s, 2s ...)
            if attempt < settings.groq_max_retries:
                time.sleep(min(0.5 * attempt, 2.0))

        raise ExtractionError(
            f"Groq extraction failed after {settings.groq_max_retries} attempts: {last_exc!r}"
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_extractor_mode() -> Literal["groq", "deterministic_eval"]:
    settings = get_settings()
    return settings.extractor_mode  # type: ignore[return-value]


def create_extractor():
    """Return an extractor instance based on the current EXTRACTOR_MODE."""
    settings = get_settings()
    if settings.extractor_mode == "deterministic_eval":
        return DeterministicExtractor()
    return GroqExtractor()
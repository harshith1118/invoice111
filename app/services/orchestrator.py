"""Analysis orchestrator.

Coordinates the full analysis pipeline:

  Upload PO + Invoice
    -> parse PDFs
    -> extract structured data (Groq or deterministic_eval)
    -> validate extracted data (Pydantic)
    -> normalize
    -> compare deterministically
    -> persist + log

Returns an ``AnalysisResponse`` dict (not a model, so the API layer can
wrap it in its own response structure).
"""

from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.db.database import get_db
from app.models.documents import Invoice, PurchaseOrder
from app.models import dump_model, walk_json
from app.models.matching import MatchResult
from app.services.extractor import (
    DeterministicExtractor,
    ExtractionError,
    create_extractor,
    get_extractor_mode,
)
from app.services.logger import StageTimer, log_stage
from app.services.matcher import compare, currencies_differ
from app.services.normalizer import normalize_text


def _validate_po(data: dict[str, Any]) -> PurchaseOrder:
    data.setdefault("document_type", "purchase_order")
    model = PurchaseOrder.model_validate(data)
    return model.clean()


def _validate_invoice(data: dict[str, Any]) -> Invoice:
    data.setdefault("document_type", "invoice")
    model = Invoice.model_validate(data)
    return model.clean()


def analyze(
    po_filename: str,
    po_bytes: bytes,
    inv_filename: str,
    inv_bytes: bytes,
) -> dict[str, Any]:
    """Run the full pipeline and return a result dict.

    Errors that indicate the document cannot be processed result in
    ``INVALID DOCUMENT``; the system does not expose raw exceptions to
    the frontend.
    """
    t0 = time.monotonic()
    db = get_db()
    settings = get_settings()
    extractor_mode = get_extractor_mode()

    # --- Initialise DB rows early so IDs are available for logging ----------
    po_doc_id: int | None = None
    inv_doc_id: int | None = None
    analysis_id: int | None = None

    po_text: str | None = None
    inv_text: str | None = None
    po_data: dict[str, Any] | None = None
    inv_data: dict[str, Any] | None = None
    errors: list[str] = []

    # We will fill in analysis_id as soon as we have valid rows.  However
    # we may need to log errors *before* we have a valid analysis.  For
    # early errors analysis_id stays None.
    def log_at(stage: str, status: str, msg: str) -> None:
        log_stage(analysis_id, stage, status, msg)

    # --- PDF parsing --------------------------------------------------------
    with StageTimer(analysis_id, "PDF_EXTRACTION", f"{po_filename}, {inv_filename}"):
        from app.services.pdf_parser import PdfProcessingError, extract_pdf_text

        for slot, filename, content, label in [
            ("po", po_filename, po_bytes, "Purchase Order"),
            ("inv", inv_filename, inv_bytes, "Invoice"),
        ]:
            try:
                result = extract_pdf_text(filename, content)
                if slot == "po":
                    po_text = result.text
                else:
                    inv_text = result.text
            except PdfProcessingError as exc:
                errors.append(f"{label}: {exc.user_message}")
                log_at(
                    "PDF_EXTRACTION",
                    "error",
                    f"{label} ({filename}): {exc.code} - {exc.detail}",
                )
                # Create a placeholder document row for traceability
                doc_id = db.insert_document(
                    document_type="po" if slot == "po" else "invoice",
                    filename=filename,
                    extracted_text=None,
                )
                if slot == "po":
                    po_doc_id = doc_id
                else:
                    inv_doc_id = doc_id

    # If either PDF completely failed to parse, we cannot proceed but we
    # still want to record the attempt so the user sees a clear history.
    if errors:
        # Create dummy analysis rows for traceability
        for slot_id, slot_name in [(po_doc_id, "PO"), (inv_doc_id, "Invoice")]:
            if slot_id is None:
                # create placeholder doc
                slot_id = db.insert_document(
                    document_type="po" if slot_name == "PO" else "invoice",
                    filename=po_filename if slot_name == "PO" else inv_filename,
                    extracted_text=None,
                )
                if slot_name == "PO":
                    po_doc_id = slot_id
                else:
                    inv_doc_id = slot_id

        analysis_id = db.insert_analysis(po_doc_id, inv_doc_id)
        log_at("START", "ok", f"extractor={extractor_mode}")
        comparison_id = db.insert_comparison(
            analysis_id, status="INVALID DOCUMENT",
            result={"reasons": errors},
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        db.update_analysis(analysis_id, processing_time_ms=elapsed)
        log_at("ERROR", "error", "; ".join(errors))
        return _build_response(
            analysis_id, comparison_id, db=db,
            status="INVALID DOCUMENT",
            reasons=errors,
            po_data=None, inv_data=None,
            field_comparisons=[], exception_count=0,
            extractor_mode=extractor_mode,
            processing_time_ms=elapsed,
        )

    # --- Persist document rows now that both PDFs parsed successfully -------
    po_doc_id = db.insert_document("po", po_filename, po_text)
    inv_doc_id = db.insert_document("invoice", inv_filename, inv_text)

    # --- Create analysis row now that we have valid doc IDs -----------------
    analysis_id = db.insert_analysis(po_doc_id, inv_doc_id)
    log_at("START", "ok", f"extractor={extractor_mode}")

    # --- Groq / deterministic extraction ------------------------------------
    with StageTimer(analysis_id, "GROQ_EXTRACTION"):
        extractor = create_extractor()
        hint_po = "purchase_order"
        hint_inv = "invoice"
        try:
            if isinstance(extractor, DeterministicExtractor):
                po_data = extractor.extract(po_text, doc_hint=hint_po)
                inv_data = extractor.extract(inv_text, doc_hint=hint_inv)
            else:
                po_data = extractor.extract(po_text, analysis_id=analysis_id)
                inv_data = extractor.extract(inv_text, analysis_id=analysis_id)
        except ExtractionError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            db.update_analysis(analysis_id, processing_time_ms=elapsed)
            comparison_id = db.insert_comparison(
                analysis_id, status="INVALID DOCUMENT",
                result={"reasons": [exc.message]},
            )
            log_at("GROQ_EXTRACTION", "error", exc.message)
            return _build_response(
                analysis_id, comparison_id, db=db,
                status="INVALID DOCUMENT",
                reasons=[f"Extraction failed: {exc.message}"],
                po_data=None, inv_data=None,
                field_comparisons=[], exception_count=0,
                extractor_mode=extractor_mode,
                processing_time_ms=elapsed,
            )

    # Normalise extractor dicts so all downstream (validation, persistence,
    # API response) receives JSON-safe data (Decimal -> str).
    po_data = walk_json(po_data)
    inv_data = walk_json(inv_data)

    # --- Pydantic validation ------------------------------------------------
    with StageTimer(analysis_id, "VALIDATION"):
        po_model: PurchaseOrder | None = None
        inv_model: Invoice | None = None
        val_errors: list[str] = []
        if po_data is not None:
            try:
                po_model = _validate_po(po_data)
            except Exception as exc:
                val_errors.append(f"PO validation error: {exc}")
        if inv_data is not None:
            try:
                inv_model = _validate_invoice(inv_data)
            except Exception as exc:
                val_errors.append(f"Invoice validation error: {exc}")
        if val_errors:
            elapsed = int((time.monotonic() - t0) * 1000)
            db.update_analysis(
                analysis_id,
                po_data=po_data,
                invoice_data=inv_data,
                processing_time_ms=elapsed,
            )
            comparison_id = db.insert_comparison(
                analysis_id, status="INVALID DOCUMENT",
                result={"reasons": val_errors},
            )
            log_at("VALIDATION", "error", "; ".join(val_errors))
            return _build_response(
                analysis_id, comparison_id, db=db,
                status="INVALID DOCUMENT",
                reasons=[f"Could not process document: {e}" for e in val_errors],
                po_data=po_data, inv_data=inv_data,
                field_comparisons=[], exception_count=0,
                extractor_mode=extractor_mode,
                processing_time_ms=elapsed,
            )
    log_at("VALIDATION", "ok", "Both models valid")

    # --- FX: fetch exchange rate only when currencies differ -----------------
    fx_rate = None
    assert po_model is not None and inv_model is not None
    if currencies_differ(po_model, inv_model):
        from app.services.frankfurter import FrankfurterClient, RateFetchError

        try:
            with StageTimer(
                analysis_id,
                "FX",
                message=(
                    f"currencies differ ({inv_model.currency} vs "
                    f"{po_model.currency}); fetching exchange rate"
                ),
            ):
                fx_rate = FrankfurterClient().get_rate(
                    inv_model.currency, po_model.currency
                )
        except RateFetchError as exc:
            log_at("FX", "error", str(exc))
        if fx_rate is not None:
            log_at(
                "FX",
                "ok",
                f"{fx_rate.from_currency}->{fx_rate.to_currency} rate "
                f"{fx_rate.rate} on {fx_rate.date} ({fx_rate.source})",
            )

    # --- Deterministic matching ---------------------------------------------
    with StageTimer(analysis_id, "MATCHING"):
        match: MatchResult = compare(po_model, inv_model, fx_rate=fx_rate)

    log_at(
        "MATCHING",
        "ok",
        f"status={match.status} exceptions={match.exception_count}",
    )

    # --- Persist results ----------------------------------------------------
    elapsed = int((time.monotonic() - t0) * 1000)
    field_comparisons_dumped = [dump_model(fc) for fc in match.field_comparisons]
    result_dict = {
        "status": match.status,
        "confidence": match.confidence,
        "reasons": match.reasons,
        "field_comparisons": field_comparisons_dumped,
        "exception_count": match.exception_count,
    }
    db.update_analysis(
        analysis_id,
        po_data=po_data,
        invoice_data=inv_data,
        processing_time_ms=elapsed,
    )
    comparison_id = db.insert_comparison(analysis_id, match.status, result_dict)

    log_at("COMPLETE", "ok", f"comparison_id={comparison_id}")

    return _build_response(
        analysis_id, comparison_id, db=db,
        status=match.status,
        reasons=match.reasons,
        po_data=po_data,
        inv_data=inv_data,
        field_comparisons=field_comparisons_dumped,
        exception_count=match.exception_count,
        extractor_mode=extractor_mode,
        processing_time_ms=elapsed,
        confidence=match.confidence,
    )


def _build_response(
    analysis_id: int,
    comparison_id: int,
    *,
    db: Any,
    status: str,
    reasons: list[str],
    po_data: dict | None,
    inv_data: dict | None,
    field_comparisons: list[dict],
    exception_count: int,
    extractor_mode: str,
    processing_time_ms: int,
    confidence: str = "LOW",
) -> dict[str, Any]:
    """Construct the API response dict from analysis data."""
    po_doc = db.get_document(
        (db.get_analysis(analysis_id) or {}).get("po_document_id")
    ) if analysis_id else None
    inv_doc = db.get_document(
        (db.get_analysis(analysis_id) or {}).get("invoice_document_id")
    ) if analysis_id else None

    return {
        "analysis_id": analysis_id,
        "comparison_id": comparison_id,
        "status": status,
        "status_reason": reasons[0] if reasons else None,
        "extractor": extractor_mode,
        "confidence": confidence,
        "po": po_data or {},
        "invoice": inv_data or {},
        "field_comparisons": field_comparisons,
        "reasons": reasons,
        "exception_count": exception_count,
        "processing_time_ms": processing_time_ms,
        "documents": {
            "po_filename": (po_doc or {}).get("filename"),
            "invoice_filename": (inv_doc or {}).get("filename"),
        },
    }
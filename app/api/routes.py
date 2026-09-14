"""HTTP API surface.

Every endpoint here directly supports the core workflow or the quest
(evaluation / history / review).  No endpoint is speculative.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings, VALID_EXTRACTOR_MODES
from app.db.database import get_db
from app.services import orchestrator

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _extractor_from_logs(logs: list[dict[str, Any]]) -> str:
    for entry in logs:
        if entry.get("stage") == "START" and entry.get("message"):
            for part in str(entry["message"]).split():
                if part.startswith("extractor="):
                    return part.split("=", 1)[1]
    return "unknown"


def _parse_json(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _detail(comparison_id: int) -> dict[str, Any]:
    """Reconstruct a full comparison detail from the SQLite rows."""
    db = get_db()
    comp = db.get_comparison(comparison_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    analysis = db.get_analysis(comp["analysis_id"])
    logs = db.get_logs(comp["analysis_id"])

    result_raw = comp.get("result_json")
    if isinstance(result_raw, str):
        try:
            result = json.loads(result_raw)
        except json.JSONDecodeError:
            result = {}
    else:
        result = result_raw or {}

    po_data = _parse_json(analysis.get("po_data_json")) or {}
    inv_data = _parse_json(analysis.get("invoice_data_json")) or {}

    po_doc = db.get_document(analysis["po_document_id"]) if analysis.get("po_document_id") else None
    inv_doc = db.get_document(analysis["invoice_document_id"]) if analysis.get("invoice_document_id") else None

    reviews = db.get_reviews(comparison_id)
    review = reviews[0] if reviews else None

    field_comparisons = result.get("field_comparisons") or []
    reasons = result.get("reasons") or []
    status = comp["status"]
    if status == "INVALID DOCUMENT":
        review_status = "none"
    elif review:
        review_status = review["decision"]
    else:
        review_status = "pending"

    return {
        "comparison_id": comparison_id,
        "analysis_id": comp["analysis_id"],
        "date": comp["created_at"],
        "result": status,
        "confidence": result.get("confidence", "LOW"),
        "status_reason": reasons[0] if reasons else None,
        "extractor": _extractor_from_logs(logs),
        "po": po_data,
        "invoice": inv_data,
        "field_comparisons": field_comparisons,
        "reasons": reasons,
        "exception_count": result.get("exception_count", 0),
        "processing_time_ms": analysis.get("processing_time_ms"),
        "documents": {
            "po_filename": (po_doc or {}).get("filename"),
            "invoice_filename": (inv_doc or {}).get("filename"),
        },
        "review": {
            "decision": review["decision"],
            "note": review.get("note"),
            "reviewer": review.get("reviewer"),
            "created_at": review["created_at"],
        } if review else None,
        "review_status": review_status,
        "logs": logs,
    }


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@router.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "extractor": settings.extractor_mode,
        "groq_configured": bool(settings.groq_api_key),
        "fx_provider": "frankfurter",
        "storage": {
            "backend": "sqlite",
            "persistence": settings.storage_persistence,
            "path": str(settings.db_path_abs),
        },
    }


@router.post("/analyze")
async def analyze(
    po: UploadFile = File(...),
    invoice: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a PO + invoice and run the full comparison pipeline."""
    if not po or not invoice:
        raise HTTPException(status_code=400, detail="Both PO and invoice files are required.")

    po_bytes = await po.read()
    inv_bytes = await invoice.read()

    if not po_bytes:
        raise HTTPException(status_code=400, detail="The PO file is empty.")
    if not inv_bytes:
        raise HTTPException(status_code=400, detail="The invoice file is empty.")

    try:
        return orchestrator.analyze(
            po_filename=po.filename or "po.pdf",
            po_bytes=po_bytes,
            inv_filename=invoice.filename or "invoice.pdf",
            inv_bytes=inv_bytes,
        )
    except Exception as exc:  # defensive barrier - no raw tracebacks to clients
        from app.services.logger import log_stage
        log_stage(None, "ERROR", "error", f"analyze failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while processing the documents. "
            "Please try again with valid PDF files.",
        )


@router.get("/comparisons")
def list_comparisons(
    result: str | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    allowed = {"MATCH", "EXCEPTION", "INVALID DOCUMENT"}
    if result and result not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"result filter must be one of: {', '.join(sorted(allowed))}",
        )
    allowed_rev = {"pending", "approved", "rejected", "none"}
    if review_status and review_status not in allowed_rev:
        raise HTTPException(
            status_code=400,
            detail=f"review_status filter must be one of: {', '.join(sorted(allowed_rev))}",
        )
    db = get_db()
    items = db.list_comparisons(result=result, review_status=review_status)
    return {"comparisons": items, "count": len(items)}


@router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: int) -> dict[str, Any]:
    return _detail(comparison_id)


@router.post("/comparisons/{comparison_id}/review")
def review_comparison(
    comparison_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Approve or reject an exception. Never auto-approves."""
    detail = _detail(comparison_id)
    if detail["result"] != "EXCEPTION":
        raise HTTPException(
            status_code=400,
            detail="Only EXCEPTION results can be reviewed.",
        )
    if detail["review"] is not None:
        raise HTTPException(
            status_code=409,
            detail="This comparison has already been reviewed.",
        )

    decision = (payload.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail='decision must be "approve" or "reject".',
        )

    note = payload.get("note")
    if note is not None:
        note = str(note).strip() or None

    db = get_db()
    db.insert_review(
        comparison_id=comparison_id,
        decision=decision,
        note=note,
        reviewer=payload.get("reviewer"),
    )
    from app.services.logger import log_stage
    log_stage(detail["analysis_id"], "REVIEW", "ok", f"decision={decision}")
    updated = _detail(comparison_id)
    return {"status": "saved", "comparison": updated["review"]}


@router.get("/dashboard/stats")
def stats() -> dict[str, Any]:
    db = get_db()
    counts = db.dashboard_stats()
    recent = db.list_comparisons()[:10]
    return {"stats": counts, "recent": recent}


# ---------------------------------------------------------------------------
# evaluation endpoints
# ---------------------------------------------------------------------------
@router.get("/evaluation/cases")
def evaluation_cases() -> list[dict[str, Any]]:
    from evaluation import load_cases
    return load_cases()


@router.post("/evaluation/run")
def evaluation_run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the evaluation suite (real pipeline) via the backend.

    body: {"mode": "groq" | "deterministic_eval"} (default: deterministic_eval)
    """
    mode = (payload or {}).get("mode") or "deterministic_eval"
    if mode not in VALID_EXTRACTOR_MODES:
        raise HTTPException(
            status_code=400,
            detail="mode must be one of: " + ", ".join(VALID_EXTRACTOR_MODES),
        )
    settings = get_settings()
    if mode == "groq" and not settings.groq_api_key:
        raise HTTPException(
            status_code=400,
            detail="groq mode requires GROQ_API_KEY to be configured in .env",
        )

    t0 = time.monotonic()
    from evaluation.run_evaluation import run_evaluation
    results = run_evaluation(mode=mode, commit=False)
    results["wall_time_ms"] = int((time.monotonic() - t0) * 1000)
    return results
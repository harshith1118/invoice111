"""Evaluation runner.

Executes the REAL analysis pipeline (PDF parse -> extract -> validate ->
normalize -> match) against the 10 synthetic fixtures and measures:

- result accuracy / exception detection / false approvals / false exceptions
- extraction accuracy (field-level vs ground truth)
- processing time
- human intervention (share routed to review)
- system failures

The extractor used is recorded per run and every result label includes it
(``extractor: groq`` or ``extractor: deterministic_eval``). Results are never
mixed between modes without clear labeling.

Usage:
    python evaluation/run_evaluation.py                  # deterministic_eval
    python evaluation/run_evaluation.py --mode groq      # requires GROQ_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running ``python evaluation/run_evaluation.py`` directly from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import reload_settings  # noqa: E402
from app.services import orchestrator  # noqa: E402
from evaluation import fixture_bytes, fixture_filename, load_expected_results, load_test_cases  # noqa: E402
from evaluation.baseline import build_baseline_table  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# extraction accuracy helpers
# ---------------------------------------------------------------------------
from app.services.normalizer import normalize_text  # noqa: E402


def _norm_item(item: dict) -> dict:
    return {
        "description": normalize_text(item.get("description")) or "",
        "quantity": str(item.get("quantity") or ""),
        "unit_price": str(item.get("unit_price") or ""),
        "total_price": str(item.get("total_price") or ""),
    }


def _norm_value(value) -> str:
    """Normalize a scored value (text -> conservative, numbers -> Decimal str)."""
    from decimal import Decimal, InvalidOperation
    if value is None:
        return ""
    s = str(value).strip()
    try:
        return format(Decimal(s), "f")
    except (InvalidOperation, ValueError):
        return normalize_text(s) or ""


def _values_equal(actual, expected) -> bool:
    return _norm_value(actual) == _norm_value(expected)


def extraction_accuracy(actual: dict | None, expected: dict | None) -> dict:
    """Field-level extraction accuracy of one document (dict vs ground truth)."""
    if expected is None:
        # No extractable ground truth (e.g. scanned case) - not scored.
        return {"fields_scored": 0, "fields_correct": 0, "accuracy": None}
    actual = actual or {}
    fields_scored = 0
    fields_correct = 0
    mismatched: list[str] = []

    expected_items = expected.get("items") or []
    actual_items = actual.get("items") or []

    for key, expected_val in expected.items():
        if key == "items":
            continue
        if expected_val is None:
            continue
        fields_scored += 1
        actual_val = actual.get(key)
        if _values_equal(actual_val, expected_val):
            fields_correct += 1
        else:
            mismatched.append(key)

    # Items: compare positionally on normalised fields
    for idx, exp_item in enumerate(expected_items):
        if idx >= len(actual_items):
            fields_scored += 4
            mismatched.append(f"items[{idx}]:missing")
            continue
        for subkey, exp_val in _norm_item(exp_item).items():
            fields_scored += 1
            act_val = _norm_item(actual_items[idx]).get(subkey)
            if _values_equal(act_val, exp_val):
                fields_correct += 1
            else:
                mismatched.append(f"items[{idx}].{subkey}")

    return {
        "fields_scored": fields_scored,
        "fields_correct": fields_correct,
        "accuracy": round(fields_correct / fields_scored, 4) if fields_scored else None,
        "mismatched_fields": mismatched,
    }


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------
def _run_case(case: dict, expected: dict) -> dict[str, Any]:
    case_id = case["case_id"]
    po_b = fixture_bytes(case_id, "po")
    inv_b = fixture_bytes(case_id, "inv")

    t0 = time.monotonic()
    try:
        result = orchestrator.analyze(
            po_filename=fixture_filename(case_id, "po"),
            po_bytes=po_b,
            inv_filename=fixture_filename(case_id, "inv"),
            inv_bytes=inv_b,
        )
        ran = True
    except Exception as exc:  # a true system failure - record, keep going
        result = {"status": "SYSTEM_FAILURE", "status_reason": f"{type(exc).__name__}: {exc}"}
        ran = False
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    actual_status = result.get("status", "SYSTEM_FAILURE")
    expected_status = expected["expected_status"]

    po_acc = extraction_accuracy(result.get("po"), expected.get("po_expected"))
    inv_acc = extraction_accuracy(
        result.get("invoice") if actual_status != "INVALID DOCUMENT" else None,
        expected.get("inv_expected"),
    )

    return {
        "case_id": case_id,
        "name": case["name"],
        "expected_status": expected_status,
        "actual_status": actual_status,
        "correct_classification": actual_status == expected_status,
        "expected_mismatch_fields": expected.get("expected_mismatch_fields", []),
        "actual_exception_fields": [
            c.get("field") for c in (result.get("field_comparisons") or [])
            if c.get("status") in ("mismatch", "missing")
        ],
        "reasons": result.get("status_reason") or (result.get("reasons") or [])[:3],
        "processing_time_ms": result.get("processing_time_ms", elapsed_ms),
        "ran": ran,
        "extractor": result.get("extractor", "unknown"),
        "po_extraction_accuracy": po_acc,
        "inv_extraction_accuracy": inv_acc,
    }


def run_evaluation(mode: str = "deterministic_eval", commit: bool = False) -> dict:
    """Execute the full suite. Returns metrics dict.

    ``commit=False`` (default) isolates the run in a throwaway SQLite file.
    """
    from app.config import get_settings
    from app.db import database as db_module

    settings = get_settings()

    # Enforce the requested extractor mode for the duration of the run.
    old_mode = settings.extractor_mode
    os.environ["EXTRACTOR_MODE"] = mode
    reload_settings()

    tmp_db: Any = None
    if not commit:
        tmp = tempfile.mkdtemp(prefix="imatch_eval_")
        from app.db.database import Database
        tmp_db = Database(Path(tmp) / "eval.db")
        db_module.use_database(tmp_db)

    cases = load_test_cases()
    expected = load_expected_results()
    expected_map = {e["case_id"]: e for e in expected}

    per_case: list[dict] = []
    for case in cases:
        exp = expected_map[case["case_id"]]
        per_case.append(_run_case(case, exp))

    try:
        summary = _summarize(per_case)
    finally:
        # restore extractor mode + DB wiring for the calling process
        os.environ["EXTRACTOR_MODE"] = old_mode
        reload_settings()
        db_module.reset_db()

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extractor": mode,
        "count": len(per_case),
        "summary": summary,
        "cases": per_case,
        "baseline": build_baseline_table(per_case),
    }
    return results


def _summarize(per_case: list[dict]) -> dict:
    n = len(per_case)
    correct = sum(1 for c in per_case if c["correct_classification"])
    expected_exceptions = [c for c in per_case if c["expected_status"] == "EXCEPTION"]
    detected_exceptions = [c for c in expected_exceptions if c["actual_status"] == "EXCEPTION"]
    false_approvals = [c for c in per_case if c["actual_status"] == "MATCH" and c["expected_status"] != "MATCH"]
    false_exceptions = [c for c in per_case if c["actual_status"] == "EXCEPTION" and c["expected_status"] == "MATCH"]
    system_failures = [c for c in per_case if not c["ran"]]

    # extraction accuracy across docs that had ground truth
    accuracies = []
    for c in per_case:
        for acc in (c["po_extraction_accuracy"], c["inv_extraction_accuracy"]):
            if acc.get("accuracy") is not None:
                accuracies.append(acc["accuracy"])
    extraction_avg = round(sum(accuracies) / len(accuracies), 4) if accuracies else None
    fields_correct = sum(a["fields_correct"] for a in
                         [c["po_extraction_accuracy"] for c in per_case] +
                         [c["inv_extraction_accuracy"] for c in per_case])
    fields_scored = sum(a["fields_scored"] for a in
                        [c["po_extraction_accuracy"] for c in per_case] +
                        [c["inv_extraction_accuracy"] for c in per_case])

    processing_ms = [c["processing_time_ms"] for c in per_case]

    # exception detection = share of true mismatches surfaced as EXCEPTION
    true_mismatches = [c for c in per_case
                       if c["expected_status"] in ("EXCEPTION", "INVALID DOCUMENT")]
    surfaced = [c for c in true_mismatches if c["actual_status"] in ("EXCEPTION", "INVALID DOCUMENT")]

    return {
        "accuracy": round(correct / n, 4) if n else 0,
        "correct": correct,
        "total": n,
        "exception_detection": round(len(surfaced) / max(len(true_mismatches), 1), 4),
        "false_approvals": len(false_approvals),
        "false_exceptions": len(false_exceptions),
        "extraction_accuracy": extraction_avg,
        "extraction_fields_correct": fields_correct,
        "extraction_fields_scored": fields_scored,
        "avg_processing_time_ms": round(sum(processing_ms) / max(n, 1), 1),
        "total_processing_time_ms": sum(processing_ms),
        "human_intervention": len([c for c in per_case if c["actual_status"] == "EXCEPTION"]),
        "human_intervention_percent": round(
            len([c for c in per_case if c["actual_status"] == "EXCEPTION"]) / max(n, 1) * 100, 1
        ),
        "system_failures": len(system_failures),
        "exception_cases_expected": len(expected_exceptions),
        "exception_cases_detected": len(detected_exceptions),
    }


def _save_results(results: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"evaluation_{results['extractor']}_{stamp}.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _print(results: dict) -> None:
    s = results["summary"]
    print("=" * 66)
    print(f"InvoiceMatch AI OS - Evaluation ({results['extractor']})")
    print("=" * 66)
    print(f"accuracy            : {s['accuracy']:.2%}  ({s['correct']}/{s['total']})")
    print(f"exception detection : {s['exception_detection']:.2%}")
    print(f"false approvals     : {s['false_approvals']}")
    print(f"false exceptions    : {s['false_exceptions']}")
    print(f"extraction accuracy : {s['extraction_accuracy']:.2%}  ({s['extraction_fields_correct']}/{s['extraction_fields_scored']})" if s.get("extraction_accuracy") is not None else "extraction accuracy : n/a")
    print(f"avg processing      : {s['avg_processing_time_ms']} ms")
    print(f"human intervention  : {s['human_intervention_percent']:.1f}%  ({s['human_intervention']}/{s['total']})")
    print(f"system failures     : {s['system_failures']}")
    print(f"extractor           : {results['extractor']}")
    print("-" * 66)
    print(f"{'case':<4} {'expected':<16}{'actual':<24}{'correct'}")
    for c in results["cases"]:
        exp = c["expected_status"]
        act = c["actual_status"] + ("*" if not c["correct_classification"] else "")
        print(f"{c['case_id']:<4} {exp:<16}{act:<24}{'OK' if c['correct_classification'] else 'FAIL'}")
    print("-" * 66)
    b = results["baseline"]
    print(f"baseline: {b['label']}")
    print(f"  total manual (synthetic) : {b['totals']['total_manual_seconds']}s")
    print(f"  total system             : {b['totals']['total_system_ms']}ms")
    if b["totals"]["system_speedup_x_sec"]:
        print(f"  system speed-up (synthetic): {b['totals']['system_speedup_x_sec']}x on elapsed time")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the evaluation suite.")
    parser.add_argument("--mode", choices=["groq", "deterministic_eval"],
                        default="deterministic_eval",
                        help="extractor mode for the run")
    parser.add_argument("--commit", action="store_true",
                        help="persist the run in the configured database")
    args = parser.parse_args()

    if args.mode == "groq":
        from app.config import get_settings
        if not get_settings().groq_api_key:
            print("ERROR: --mode groq requires GROQ_API_KEY to be configured in .env")
            return 2

    results = run_evaluation(mode=args.mode, commit=args.commit)
    out = _save_results(results)
    _print(results)
    print(f"\nresults saved to: {out}")

    # Exit code: non-zero when there are system failures (runner threw) or
    # classification surprises on the deterministic baseline.
    failing = all(c["correct_classification"] for c in results["cases"])
    if results["summary"]["system_failures"]:
        return 1
    if not failing and args.mode == "deterministic_eval":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
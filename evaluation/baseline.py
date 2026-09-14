"""Synthetic / Proxy baseline for manual invoice-PO comparison.

The numbers below are ESTIMATES of a manual workflow (open PO, open invoice,
inspect fields, compare, record, and for exceptions investigate). They do NOT
represent measurements from a real customer or real finance team.

They are explicitly labeled "Synthetic / Proxy Baseline" everywhere they are
used, so they are never presented as real user data.
"""

from __future__ import annotations

BASE_MANUAL_SECONDS = 120      # open, read, compare, record a pair of documents
EXCEPTION_EXTRA_SECONDS = 90   # investigate the discrepancy + decide

STEPS = {
    "pdf_opening": 15,
    "field_inspection": 30,
    "line_item_comparison": 40,
    "decision_and_recording": 20,
}


def synthetic_baseline_seconds(exception: bool) -> int:
    """Synthetic per-case manual processing time (clearly not measured)."""
    return BASE_MANUAL_SECONDS + (EXCEPTION_EXTRA_SECONDS if exception else 0)


def build_baseline_table(case_times: list[dict]) -> dict:
    """Build the baseline-vs-system comparison table for the report.

    ``case_times`` is a list of per-case measured results from the
    evaluation run:  [{case_id, actual_status, processing_time_ms}, ...]
    """
    rows = []
    for case in case_times:
        is_exception = case.get("actual_status") == "EXCEPTION"
        baseline_s = synthetic_baseline_seconds(is_exception)
        system_ms = case.get("processing_time_ms") or 0
        rows.append(
            {
                "case_id": case.get("case_id"),
                "manual_baseline_s": baseline_s,
                "system_ms": system_ms,
                "manual_touch": "100%" if is_exception else "0%",
                "system_touch": "yes" if is_exception else "no",
                "baseline_manual_seconds": baseline_s,
            }
        )
    # Aggregate
    total_manual_s = sum(r["manual_baseline_s"] for r in rows)
    total_system_ms = sum(r["system_ms"] for r in rows)
    human_cases = [r for r in rows if r["system_touch"] == "yes"]
    return {
        "label": "Synthetic / Proxy Baseline",
        "disclaimer": (
            "Manual times are synthetic estimates of the described manual "
            "workflow, not real customer measurements."
        ),
        "rows": rows,
        "totals": {
            "total_manual_seconds": total_manual_s,
            "total_system_ms": total_system_ms,
            "system_speedup_x_sec": round(total_manual_s / (total_system_ms / 1000), 2)
            if total_system_ms else None,
            "manual_human_intervention_percent": (
                round(len([r for r in rows if r["manual_touch"] != "0%"]) / max(len(rows), 1) * 100, 1)
            ),
            "system_human_intervention_percent": (
                round(len(human_cases) / max(len(rows), 1) * 100, 1)
            ),
        },
    }
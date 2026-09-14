# Case Study: InvoiceMatch AI OS — first measurement

This document reports the **measured** results of the MVP on its 10-case
evaluation suite, plus a clearly **labeled synthetic** baseline for context.
No customer data is involved anywhere.

- Environment: Python 3.13.5 · pypdf 6.18.1 · reportlab-generated fixtures
- Extractor under test: `deterministic_eval` (offline, reproducible)
- The Groq LLM extractor is exercised only when a `GROQ_API_KEY` is provided;
  those numbers are recorded separately in `evaluation/results/` with their
  own `extractor` tag and are never mixed with the deterministic run.

> Full JSON record:
> `evaluation/results/evaluation_deterministic_eval_20260913T183440Z.json`

## Scenario

A mid-size enterprise receives invoices that reference purchase orders. A
finance analyst must check vendor, PO number, currency, subtotal, tax, total,
and every line item — then file a decision. We simulated 10 representative
pairs and challenged the system to classify them by running the **real**
pipeline (PDF parse → extract → normalize → match).

## The 10 cases

| # | Scenario | Expected | Actual |
|---|---|---|---|
| 1 | Perfect match | MATCH | MATCH |
| 2 | Quantity differs (2 vs 3) | EXCEPTION | EXCEPTION |
| 3 | Unit price differs (500 vs 600) | EXCEPTION | EXCEPTION |
| 4 | Total differs (763 vs 765) | EXCEPTION | EXCEPTION |
| 5 | Vendor differs | EXCEPTION | EXCEPTION |
| 6 | PO number differs | EXCEPTION | EXCEPTION |
| 7 | PO reference missing on invoice | EXCEPTION | EXCEPTION |
| 8 | Three matching line items | MATCH | MATCH |
| 9 | Formatting variation (spacing/case) | MATCH | MATCH |
| 10 | Scanned (image-only) invoice | INVALID | INVALID |

## Measured results (deterministic extractor)

| Metric | Result |
|---|---|
| Classification accuracy | **100%** (10/10) |
| Exception detection (of 6 true mismatches) | **100%** (6/6) |
| False approvals | **0** |
| False exceptions | **0** |
| Field-level extraction accuracy | **100%** (204 of 204 fields scored) |
| System failures | **0** |
| Avg processing per pair | ~1.8 s (this run) |
| Human-review routing | 60% (the 6 EXCEPTION cases) |

Notable behaviors:

- Case 9 (spacing/case variation) matches because normalization collapses
  whitespace and folds case before comparing.
- Case 10 (scanned invoice) is rejected at the PDF layer and the pipeline
  **bails early** rather than wasting an extraction call on the healthy PO.
- A field absent on *both* documents (e.g. both omitting subtotal) is treated
  as consistent rather than an exception.

## Synthetic baseline (for context only)

Manual per-pair effort is modeled as ~120 s + ~90 s when a discrepancy needs
investigation. These are **estimates of the described workflow**, not
customer measurements, exactly as configured in `evaluation/baseline.py`.

| | System | Manual (synthetic) |
|---|---|---|
| Total elapsed, 10 pairs | ~17.9 s (17919 ms) | ~29 min (1740 s) |
| Effective speed-up | — | ~97x on elapsed time |
| Work routed to a human | 60% (6 cases) | 60% (investigate the same 6) |

The headline claim is deliberately limited: **the machine performs the
mechanical field-by-field comparison correctly on every tested pair and still
routes exactly the cases a human should review.** It does not pretend to
approve discrepancies.

## Rerun

```bash
python evaluation/run_evaluation.py                 # deterministic extractor
python evaluation/run_evaluation.py --mode groq     # LLM extractor (needs key)
```

The same suite is part of `pytest` (`tests/test_evaluation.py`), so the
10/10-accurate result is regression-guarded.

## Caveats and next measurements

- Only one layout family per document so far; real-world invoices are wilder.
- `--mode groq` numbers are pending until a `GROQ_API_KEY` is configured.
- All 10 fixtures are USD-only (same currency), so the Frankfurter FX path is
  **not** exercised by this baseline number. Cross-currency conversion
  (converted amounts, fx evidence, rate-unavailable handling, "same currency
  never calls the API") is covered by the automated test suite in
  `tests/test_matcher.py` and `tests/test_orchestrator.py`.
- Image-only PDFs are rejected today (by design). OCR is a roadmap item.
- The speed-up is synthetic; a real time-and-motion study belongs on the
  roadmap, not in the MVP report.
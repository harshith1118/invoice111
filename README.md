# InvoiceMatch AI OS

Compare Purchase Orders against supplier invoices and auto-classify them as
`MATCH`, `EXCEPTION`, or `INVALID DOCUMENT`. Uploads route to a human review
workflow, results are stored in SQLite, and cross-currency documents are
converted via the public Frankfurter exchange-rate API.

Built as a quest deliverable: a small, production-shaped MVP with a
reproducible 10-case evaluation and an automated test suite.

## What it does

1. **Upload** a PO PDF + an invoice PDF.
2. **Extract** structured data (vendor, PO number, line items, totals) with
   a Groq LLM extractor (or a deterministic extractor for test/eval only).
3. **Normalize** text and numbers (thousand separators, currency symbols,
   case/spacing folding).
4. **Match** vendor, PO number, currency, subtotal, tax, total, and each line
   item (quantity, unit price, total price) with configurable tolerances.
5. **Convert** (only when currencies differ): invoice amounts are converted
   to the PO currency using a Frankfurter exchange rate; the rate, date and
   source are recorded as comparison evidence. Quantities are **never**
   converted. Same-currency documents are never sent to the FX API.
6. **Classify**:
   - `MATCH` — everything agrees (auto-clear, no review).
   - `EXCEPTION` — at least one field differs or is missing; goes to review.
   - `INVALID DOCUMENT` — a file is not a PDF, empty, corrupt, oversized,
     encrypted, or scanned/image-only (no text layer). No comparison is made.
7. **Review** exceptions: approve or reject, with a note.

A field that is missing on **both** sides (e.g. no subtotal printed on either
document) is treated as *consistent*, not an exception. If an FX rate cannot
be fetched, the comparison is **never silently approved**: it becomes an
`EXCEPTION` for a human to review.

## Quick start

```bash
# 1. set up
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. configure (see .env.example)
copy .env.example .env          # then edit .env

# 3. run
python run.py                   # http://127.0.0.1:8000
```

> Windows terminal note: if `pytest` output looks garbled, set
> `PYTHONIOENCODING=utf-8` (and optionally `PY_COLORS=0`) in the shell. This
> only affects console rendering, not the results.

The app starts in `groq` extraction mode by default. To run purely offline use:

```bash
set EXTRACTOR_MODE=deterministic_eval
python run.py
```

## Configuration

All settings live in env vars (see `.env.example`). Key ones:

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` | — | API key for the Groq LLM extractor |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Model used for extraction |
| `EXTRACTOR_MODE` | `groq` | `groq` or `deterministic_eval` (eval-only) |
| `AMOUNT_TOLERANCE` | `0.01` | Max absolute money difference per field |
| `QUANTITY_TOLERANCE` | `0` | Max absolute quantity difference per item |
| `PDF_MAX_SIZE_MB` | `10` | Upload size limit |
| `DB_PATH` | `data/invoicematch.db` | SQLite file (ephemeral under `VERCEL=1`; see Deployment) |
| `FRANKFURTER_BASE_URL` | `https://api.frankfurter.dev/v1` | Exchange-rate API (public, no key) |
| `FRANKFURTER_TIMEOUT_SECONDS` | `5` | Timeout per FX request |

Secrets are never logged or committed. `.env`, `credentials/`, and
`data/` are gitignored.

## Deployment (Vercel)

The app deploys to Vercel as-is (FastAPI preset detects `app/main.py`; the
existing static frontend and `/api/*` routes run from one function/domain).
See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for exact steps and required
environment variables. Deployed SQLite runs under `/tmp` and is explicitly
**ephemeral** (reported as `storage.persistence` in `/api/health`).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/analyze` | Upload PO + invoice (multipart `po`, `invoice`) |
| GET | `/api/comparisons` | List comparisons (`?result=`, `?review_status=`) |
| GET | `/api/comparisons/{id}` | Full detail incl. field evidence + logs |
| POST | `/api/comparisons/{id}/review` | `{"decision": "approve"\|"reject", "note": ...}` |
| GET | `/api/dashboard/stats` | Counts by status + recent history |
| GET | `/api/evaluation/cases` | The 10 evaluation cases |
| POST | `/api/evaluation/run` | Run evaluation (`{"mode": ...}`) |
| GET | `/api/health` | Status, extractor mode, FX provider |

Interactive docs: `http://127.0.0.1:8000/docs`.

## Frontend

Static vanilla JS/CSS in `frontend/`, served by FastAPI:

- `index.html` — dashboard
- `new.html` — upload; also shows the result (`?id=<comparison_id>`)
- `review.html` — human review queue
- `history.html` — past comparisons
- `evaluation.html` — evaluation summary + rerun

## Evaluation

10 synthetic PDF pairs in `evaluation/fixtures/` exercise match, quantity
mismatch, unit-price mismatch, total mismatch, vendor mismatch, PO-number
mismatch, missing PO reference, multi-item match, formatting-variation match,
and a scanned invoice (INVALID).

```bash
python evaluation/run_evaluation.py                     # deterministic extractor
python evaluation/run_evaluation.py --mode groq         # needs GROQ_API_KEY
```

Runs the **real** pipeline (PDF → extract → normalize → match) in an isolated
database, computes accuracy, exception detection, false approvals/exceptions,
field-level extraction accuracy, processing time, and a clearly-labeled
*synthetic* human baseline for comparison. Results are written to
`evaluation/results/evaluation_<mode>_<timestamp>.json` and every outcome is
tagged with the extractor that produced it.

## Tests

```bash
set EXTRACTOR_MODE=deterministic_eval
python -m pytest -q
```

109 tests cover the PDF parser (valid/empty/corrupt/oversized/non-PDF), number
normalization (`2,000.00` → 2000.00), the matcher (all mismatch classes +
tolerances + formatting + cross-currency conversion with fx evidence of
rate/date/source), the Frankfurter client (URL building, JSON parsing, HTTP and
network error handling, invalid codes), the full HTTP API
(analyze/list/detail/review, 409/400 guards), end-to-end FX wiring through the
pipeline (rate ok / rate failure / same-currency must not call the API), and
evaluation reproducibility. The 10-case evaluation runs **once** per session as
a pytest module (it is ~20s); each real-parser and API test uses an isolated
temporary database.

## Project layout

```
app/
  api/routes.py           HTTP endpoints
  config.py               settings (env/.env)
  db/database.py          SQLite schema + access
  main.py                 FastAPI app, static mount
  models/                 Pydantic contracts (documents, matching, reviews)
  services/
    extractor.py          Groq + deterministic extractors
    pdf_parser.py         PDF validation + text layer extraction
    normalizer.py         text/number normalization
    matcher.py            deterministic compare + evidence (incl. FX conversion)
    orchestrator.py       analyze() pipeline + persistence
    frankfurter.py        Frankfurter exchange-rate client (cross-currency only)
    logger.py             structured stage logs
scripts/generate_fixtures.py   regenerates the eval PDFs
evaluation/               fixtures, cases, expected results, runner, baseline
frontend/                 static UI
tests/                    pytest suite
docs/                     architecture, case study, AI-collaboration notes
```

## Docs

- `docs/ARCHITECTURE.md` — component overview + data flow.
- `docs/DEPLOYMENT.md` — Vercel deployment steps + environment variables.
- `docs/CASE_STUDY.md` — measured evaluation results (100% deterministic).
- `docs/AI_COLLABORATION.md` — how AI tooling was used to build this.

## Roadmap (explicitly NOT in the MVP)

- OCR / image-only PDFs (rejected today)
- Auth, multi-user review queues, audit trail beyond today's review log
- Async processing / queues / webhooks
- User sign-off instead of single-click approve/reject
- Multi-language invoice layouts and broader real-world PDF diversity
- More currency pairs / configurable FX angle beyond the Frankfurter snapshot

These are intentionally deferred; the current scope matches the PRD.
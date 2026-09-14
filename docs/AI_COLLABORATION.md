# AI Collaboration Notes

This project was built as an interactive, human-plus-AI engineering session.
These notes record how the tooling actually influenced the work — what it
caught, what it repeated, and where the human had to override or decide.

## Division of labour

- The **human** set requirements from a PRD-like spec (`Untitled-prd.txt`),
  made product decisions (see below), supplied environment facts (Python
  version, installed packages, Windows), and reviewed every milestone.
- The **AI** generated the bulk of the code in service-sized files, kept a
  running model of the whole system, wrote the fixture generator, evaluation
  runner, the 109-test pytest suite, and this documentation — then verified
  each milestone by executing commands.

## Design decisions made by the human

- Build directly in the project folder; no extra wrapper package.
- Evaluation covers **both** extractors and every result is labeled with the
  extractor that produced it (`extractor=groq` vs `extractor=deterministic_eval`).
- The deterministic extractor is **evaluation/test-only**, never a silent
  production fallback; `EXTRACTOR_MODE` picks one explicitly.
- No React, no auth, no async workers, no OCR in the MVP. Scanned PDFs are
  deliberately rejected as `INVALID DOCUMENT`.
- Evaluation case 7 (missing PO reference) resolves to `EXCEPTION`, not
  `INVALID`.
- Case 10 (scanned invoice): the pipeline bails early, so the healthy PO is
  not even extracted — an intentional cost-saving behavior.
- **Second external integration = Frankfurter, not Google Sheets.** The
  original plan used Google Sheets for result export, but the human cannot use
  Google Cloud billing for this project, so the integration was swapped (at
  the human's request) to the public Frankfurter exchange-rate API — no API
  key, used only for cross-currency documents. No new integrations were added;
  the core Groq + deterministic-matching + human-review architecture is
  unchanged.

## Bugs the AI introduced and the tests caught

| Bug | Where it bit | Fix |
|---|---|---|
| `dump_model` referenced but not imported | evaluation runner crashed | import from models |
| `Decimal` not JSON-serializable in API payloads | /api/analyze response | `walk_json` normalization; money serialized as strings |
| `parse_number` mis-read `2,000.00` | totals became 2.00 | thousand-separator handling + regression test |
| Result/analysis JSON columns returned as raw strings | `/api/comparisons/{id}`, review → 500 | parse JSON in `_detail` (`_parse_json`) |
| Matcher counted a field missing on both sides as an exception | spurious EXCEPTIONS | both-absent is consistent → match + test |
| Evaluation ran once per pytest method (~3 min) | suite appeared to hang | module-scoped fixture runs it once (~25 s) + test |
| Synthetic baseline read `case["status"]`, runner wrote `actual_status` | baseline claimed 0% human touch on exceptions | read `actual_status` + regression tests |

All fixes are covered by tests so they stay fixed.

## Where the human's judgment overrode the AI

- **Scope control**: the AI repeatedly had to be told not to add components
  that exceed the PRD (auth, OCR, async queues, a second frontend framework).
- **Stack choices**: react proposals were declined; vanilla JS + FastAPI static
  serving was kept.
- **Evaluation honesty**: the AI proposed a believable-looking manual-time
  baseline; the human required it be labeled *Synthetic / Proxy* and kept out
  of core metrics. The case study reports only measured numbers for system
  behavior.

## How the AI kept itself honest

- Secrets handling: keys only via `.env`; API keys never logged; gitignored
  `.env`, `credentials/`, `data/`.
- `INVALID DOCUMENT` handling is a hard pipeline stop; scanning is not
  silently faked.
- Every evaluation result file records which extractor produced it.
- No unmet "AI-ocrites"; deviations from the PRD spec are documented in README
  roadmap and this file.

## Remaining gaps

- `--mode groq` measurement awaits a real `GROQ_API_KEY`.
- The Frankfurter integration is exercised offline via stubbed `urllib` in the
  test suite; a live cross-currency run against the public API is a follow-up
  when real multi-currency sample documents are available.
- Real-world PDF diversity and a real time-and-motion study are future work.
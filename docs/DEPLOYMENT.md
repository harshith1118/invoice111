# Deploying InvoiceMatch AI OS to Vercel

This application is the existing FastAPI + SQLite + vanilla-JS (HTML/CSS/JS)
stack, deployed to Vercel as a **single project** under one domain. No
framework rewrites were needed: Vercel's FastAPI preset auto-detects the app,
and FastAPI itself serves both the `/api/*` routes and the `frontend/` static
files from the same origin.

## How the deployment works

1. Vercel detects the FastAPI framework preset via `fastapi` in
   `requirements.txt`.
2. It looks for a `FastAPI` instance named `app` at a supported entrypoint.
   `app/main.py` already exposes `app = create_app()` — this matches the
   `app/` + `main.py` entrypoint convention, so **no new Python entrypoint
   file is required**.
3. Vercel routes *every* request to this function ("the app you run locally
   deploys as-is"). FastAPI's existing route table handles `GET /`, `/new`,
   `/history`, `/review`, `/evaluation`, `/css/*`, `/js/*`, `/api/*`, `/docs`.
4. `vercel.json` configures the function (`maxDuration: 60`, and
   `includeFiles` so the runtime non-Python assets — `frontend/**`,
   `evaluation/**` (PDF fixtures), `sample_data/**` — are bundled into the
   serverless function. `includeFiles` must be a single glob *string*
   (brace-expansion `{a,b}/**` — the schema rejects arrays). Without this,
   `app/main.py`'s `StaticFiles` mount crashes the function at cold start
   because the directory is missing from the bundle). No `rewrites` are
   needed — the app runs exactly as it does locally.

## Required environment variables

Set these in **Vercel → Project → Settings → Environment Variables**. Secrets
are never committed (`/api/health` only reports whether `groq_configured` is
true).

| Variable | Deployed value | Notes |
|---|---|---|
| `GROQ_API_KEY` | the real key | Required for real extraction. `openai/gpt-oss-120b` is a good free-keys model; `llama-3.3-70b-versatile` is Enterprise-only on Groq. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Optional; config default is `llama-3.3-70b-versatile`. |
| `EXTRACTOR_MODE` | `groq` | `groq` or `deterministic_eval` (offline eval/test only). |
| `FRANKFURTER_BASE_URL` | `https://api.frankfurter.dev/v1` | Optional override. |
| `FRANKFURTER_TIMEOUT_SECONDS` | `5` | Optional override. |
| `DB_PATH` | *(unset)* | Leave unset so deployment uses the ephemeral `/tmp` database (see below). |

`VERCEL` and `VERCEL_ENV` are injected by Vercel automatically — do not add
them manually.

## Storage on Vercel (important)

Vercel serverless functions run on **read-only filesystems** with only a
temporary, per-instance `/tmp` directory. Consequences:

- When `VERCEL=1` and `DB_PATH` is not set, the app automatically places its
  SQLite database in `/tmp/invoicematch.db` and marks storage
  `persistence: "ephemeral"` in `GET /api/health`.
- The ephemeral database **may reset between requests** and is not shared
  across function instances. History/review data written in one request can
  disappear in the next. This is a demo-mode limitation, and the app is
  explicitly honest about it (`/api/health` → `storage.persistence`).
- No durable production persistence is ever claimed. For durable hosted
  persistence outside the MVP scope, the app would need a managed database —
  this is intentionally not part of the deployment.

## PDF uploads

Uploads are read fully **in-memory** during the request
(`await po.read()` → bytes → `orchestrator.analyze(...)`); nothing is written
to disk. No upload storage is required.

## Evaluation fixtures

`evaluation/fixtures/po_01..10.pdf` and `inv_01..10.pdf` ship with the
deployment (`.vercelignore` does **not** exclude `evaluation/`) so
`POST /api/evaluation/run` works in production. The endpoint writes its
results to a throwaway SQLite file regardless of the main DB.

## Timeouts and plans

- The whole app runs as **one** Vercel function configured with
  `maxDuration: 60` (the Hobby-plan maximum). Normal comparisons and the
  deterministic evaluation (~8 s) fit comfortably.
- A **groq-mode evaluation** runs 10 live LLM calls; on Hobby it can exceed
  60 s and time out. Use `deterministic_eval`, or raise `maxDuration` (Pro
  plans) in `vercel.json` if live-Groq evaluation is required.
- Comparisons are synchronous; larger PDFs / many items increase latency
  proportionally. 10 MB uploads stay well within request limits.

## Deployment steps

```bash
# 0) Prerequisites
#    - Node/npm + Vercel CLI (npm i -g vercel) — already installed here.
#    - The project must be in a git repo (quest/ is currently untracked).
#    - The user's real GROQ_API_KEY must NOT be in any committed file.

cd C:\Users\Lenovo\Desktop\quest

# 1) Log in
vercel login

# 2) Link this directory to a Vercel project (creates .vercel/, gitignored)
vercel link

# 3) Preview deploy (uses production env vars from the linked project
#    settings; non-production build)
vercel

# 4) Production deploy
vercel --prod
```

After the first deploy, add the environment variables in the Vercel dashboard
(or answer the `vercel env add GROQ_API_KEY` prompts) and redeploy.

### Local development through the Vercel dev server

The Vercel-compatible local dev command runs the **same** FastAPI app:

```bash
vercel dev          # serves the app with Vercel's routing/function wrapper
```

The app can also run directly with the production start command:

```bash
python run.py       # uvicorn app.main:app, http://127.0.0.1:8000
```

## What did not change for deployment

- Frontend stack (HTML/CSS/vanilla JS in `frontend/`) — unchanged.
- FastAPI + SQLite — unchanged; SQLite stays the local/reproducible engine.
- Groq + Frankfurter integrations — unchanged.
- `app/main.py` still mounts the static frontend at `/`; all API traffic is
  same-origin.
- No new Docker / React / Next.js / database integrations.

## Verification checklist applied

- `python -m pytest -q` → 109 passed.
- `python evaluation/run_evaluation.py --mode deterministic_eval` → 10/10,
  extraction 204/204, 0 failures.
- `GET /api/health` → `"storage":{"backend":"sqlite","persistence":"ephemeral"}`
  under `VERCEL=1`; `"durable"` locally.
- `GET /api/health` reports `groq_configured` (true only when the key is set).
- Frontend, New Comparison, Groq extraction, Frankfurter cross-currency path,
  and the Evaluation Suite were exercised over the ASGI entrypoint.
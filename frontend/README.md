# Horizon — churn-risk frontend

A dependency-free static UI that drives the Phase 8 FastAPI backend. It **never
computes, infers, or overrides** a risk level, score, rank, or confidence — it
triggers runs and renders exactly what the API returns.

## Serving

The FastAPI app mounts this directory at `/` when `FRONTEND_DIR` exists
(default `frontend/`). Explicit API routes keep precedence.

```bash
# .env
API_ENABLE_WRITES=true
API_KEY=<your-key>
FRONTEND_DIR=frontend/
```

```bash
churn-survival-api      # http://127.0.0.1:8000
```

Open the URL, paste the API key into the sidebar (stored in `localStorage`),
then Upload → Run → Report.

## Screens

- **Upload / Trigger** — lists server files (`GET /raw-files`, classified as
  customer **dataset** vs support **threads**), accepts a file upload
  (`POST /uploads`), and triggers `POST /runs`. A support-threads JSON is loaded
  via `GET /raw-files/{name}` and passed as Node 3 `support_data`.
- **Run Status** — polls `GET /runs/{id}` every 2s, renders `stage` /
  `execution_status` verbatim, and branches on terminal state.
- **Mapping Confirmation** — `POST /mappings/draft`, editable audit table (the
  audited transform names are shown), `POST /mappings/confirm`, then re-triggers
  a **new** run (a confirmed mapping changes the routing-inclusive `run_id`).
- **Ranked Report** — `GET /runs/{id}/ranked-accounts` (rank / level / score /
  confidence), a separate insufficient-data section, and a detail drawer whose
  explanation text carries a visible **LLM-drafted vs template** tag.
- **Run History** — `GET /runs` newest-first with a UI-only status filter and
  `superseded_by` lineage.
- **Models** — `GET /models` inspection only.

## Input kinds

Two kinds of file share `RAW_DATA_DIR`, and they are **not** interchangeable:

| kind        | extensions            | role                                   | run field     |
|-------------|-----------------------|----------------------------------------|---------------|
| `dataset`   | `.csv`, `.xlsx`, `.xls` | Node 1 customer data                  | `raw_path`    |
| `support`   | `.json`               | Node 3 support threads (array of objects) | `support_data` |

`GET /raw-files` lists only these; anything else is hidden so the UI cannot
offer an unrunnable file as a customer dataset. Passing a JSON as `raw_path` is
a `422` by design (Node 1 ingests tabular data only).

## Hard rules honored

1. Decision values render exactly as returned; nothing is recomputed.
2. Every GET is treated as idempotent and repeatable.
3. `X-API-Key` is optional and persisted per browser; 401/403 render as clear
   "writes disabled / key required" states.
4. No `run_id` or fingerprint is ever constructed client-side.
5. `created_at`/`finished_at` are display-only ("started N ago"); pipeline state
   always comes from `execution_status`/`stage`.
6. Responses are parsed by `content-type`: JSON endpoints yield objects, while
   `text/plain` (support threads) and `text/html` (static report) yield strings.
   The wrapper never blind-parses, so the raw-files JSON is returned verbatim for
   the caller to parse. The report screen links to the server-rendered static
   report in a new tab.

## Layout

```
index.html            shell (sidebar + canvas)
static/app.css        tokens, light/dark, responsive, tabular figures
static/api.js         single fetch wrapper (auth, error normalization)
static/router.js      hash router
static/app.js         shell wiring (nav, theme, banner, routing)
static/components/    ui.js, stageTracker.js
static/views/         upload, runStatus, mapping, report, history, models
```

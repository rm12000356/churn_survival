# Phase 8 — Persistence & API

**Status:** complete (2026-09-27)
**Architecture refs:** §8.5 (model & mapping persistence), §8.7 (API/serving), §8.9 (testing), §8.11 (dependencies).
**Roadmap:** Phase 8, Tasks 8.1–8.3.

## Scope

Turn the Phase 7 deterministic pipeline into a **serveable** system:

1. persist every run's outputs (Node 1–5 + HTML report) and its artifacts;
2. expose them over a small authenticated FastAPI app that **never recomputes on read**;
3. execute pipeline runs **asynchronously** (trigger returns immediately; clients poll);
4. provide a queryable run history backed by a load-bearing SQLite index;
5. retention/GC and a full-pipeline E2E suite.

## Decision record

### D-P1 — Routing-inclusive, content-addressed run identity

`run_id = sha256[:16](raw_digest, support_digest, config_versions, reference_date, routing_identity)`
where `config_versions` covers Node 1–5 **plus** action rules / sources / identity
configs, and `routing_identity` is the resolved router decision (adapter name +
version + confidence, or no-match).

The mapping registry determines which adapter transforms the raw bytes, so it is
**input configuration**. Excluding it would break the same invariant Node 2's
`derive_dataset_version` and Node 4/5 determinism rest on: *identity tracks
everything that can change the output*. Consequence: after a mapping is confirmed
the identity changes, so the retry is a **new** run and the stopped run survives
as audit — the mapping flow can never present a stale ID as a completed result.

`orchestration/identity.py` is decision-free: it imports only stdlib + schemas and
never touches `node1`..`node5`. The trigger forms an identity by fingerprinting +
routing (cheap, deterministic, no node execution); a direct test asserts no node
entry point is called while computing a trigger identity.

### D-P2 — The SQLite index is load-bearing

`orchestration/index.py` (stdlib `sqlite3`, connection-per-operation, WAL) holds
one `runs` table: status, stage, raw path/digest, reference date, adapter, counts,
model version, ranked/insufficient/report counts, mapping version, routing fields,
`superseded_by`, `error_code`, and operational timestamps. It backs `GET /runs`
and `GET /runs/{id}`. It **self-heals** from `runs/*/summary.json` (or `state.json`).

### D-P3 — Single-writer-of-decisions

`run_pipeline` is the only code that produces a risk level/rank/score/confidence.
Read endpoints deserialize stored bytes only. A GET that is slow stays slow; it is
never "optimized" by recomputing. Enforced by a contract test that monkeypatches
every node entry point to raise and then exercises every read endpoint.

### D-P4 — Asynchronous execution

`POST /runs` computes the identity without running, records `RUNNING`, enqueues a
single background `ThreadPoolExecutor` job (`RUN_MAX_WORKERS=1` by default), and
returns `202`. The worker calls `run_pipeline` once, persists via `RunStore`, and
writes the terminal status. Clients poll `GET /runs/{id}`. Startup marks stale
`RUNNING` rows `INTERRUPTED` (single process).

### D-P5 — Execution status is separate from pipeline status

`RunExecutionStatus` (`PENDING`, `RUNNING`, `COMPLETED`, `STOPPED_NEEDS_MAPPING`,
`STOPPED_VALIDATION`, `FAILED`, `INTERRUPTED`) is the lifecycle stored in the index;
`PipelineStatus` remains the graph's own outcome (recorded as `pipeline_status`).

### D-P11 — `POST /runs` status matrix (`?force=true`)

| existing status | default | `force=true` |
|---|---|---|
| `PENDING` / `RUNNING` | `202` (existing id, no duplicate) | `409` |
| `COMPLETED` | `200` cached | resubmit in place, `202` |
| `FAILED` / `INTERRUPTED` | resubmit in place, `202` | resubmit, `202` |
| `STOPPED_NEEDS_MAPPING` / `STOPPED_VALIDATION` | `200` cached (+fingerprint) | **`400`** |

`STOPPED_*` are deterministic given their (routing-inclusive) identity, so the
config change that would make a retry succeed produces a new id; forcing the
identical decision is rejected. `supersedes_run_id` links a new run to a superseded
one (`superseded_by`) purely for UI/audit — it never reconstructs inputs.

### D-P12 — Legacy rows surface `unknown_pre_migration`

Self-heal never guesses routing for rows without routing metadata: the routing
fields are `null` and `routing_identity_source = unknown_pre_migration` (never an
empty string that could read as valid data).

### D-P6 — Retention / GC (hybrid, manual)

`churn-survival gc [--models N] [--runs N] [--pending-ttl-days D] [--recover]`:

- **count-based** for model artifacts (`MODEL_RETENTION_MAX`) and runs
  (`RUN_RETENTION_MAX`) — keep newest by mtime;
- **TTL-based** for pending states (`PENDING_RUN_TTL_DAYS`): `STOPPED_NEEDS_MAPPING`,
  `STOPPED_VALIDATION`, `INTERRUPTED` run directories and unconfirmed
  `config/mappings/drafts/*.json`;
- **confirmed** `config/mappings/map_*.json` are never pruned (they are the routing
  configuration);
- `--recover` marks stale `RUNNING` rows `INTERRUPTED`.

`GC_ON_STARTUP=false` by default — no hidden mutation. **Note:** `gc` prunes drafts
in the *active* `CONFIG_DIR`; in a dev checkout that can remove committed drafts, so
pass `--pending-ttl-days 0` to skip that rule.

### D-P7 — Auth / access control

`API_KEY` unset → reads open (demo). Set → every endpoint requires the configured
header. `API_ENABLE_WRITES=false` by default → all `POST`s return `403`; enabling
writes **requires** an `API_KEY` or `Settings` construction fails (fail loud).
`POST /mappings/confirm` is always write-gated + authenticated, routes through
`orchestration/mapping.persist_confirmed_mapping` (the gate) and records the actor
(`X-Actor`, default `api-key`) as the mapping's `confirmed_by`.

## Surfaces

**Store layout** (`RUN_DIR`, default `runs/`):
`<run_id>/state.json`, `summary.json`, `node1..4.json` (present only), `node5.json`,
`report.html`; plus `runs/index.sqlite`.

**Read endpoints:** `GET /health`, `/runs`, `/runs/{id}`,
`/runs/{id}/report`, `/runs/{id}/report.html`, `/runs/{id}/ranked-accounts`,
`/runs/{id}/node1..4`, `/models`, `/models/{model_version}` (inspect only).

**Write endpoints:** `POST /runs` (trigger), `POST /mappings/draft`,
`POST /mappings/confirm`.

**CLI:** `churn-survival run … --persist-run [--run-dir <p>]`,
`churn-survival gc …`, `churn-survival-api` (uvicorn).

**Dependencies:** `fastapi`, `uvicorn`, `httpx` added to the `dev` extra so
`pip install -e ".[dev]"` + `pytest` run the API tests; the `api` extra remains for
production.

## Verification

- `tests/persistence/` — run-id stability (routing/config sensitivity),
  decision-free trigger identity, route stability, save/load/list, index
  upsert/self-heal, legacy `unknown_pre_migration`, GC count+TTL, stale-RUNNING
  recovery.
- `tests/api/` — health, async trigger → poll, idempotent re-trigger, force
  matrix, **no-recompute-on-read**, read idempotency, auth `401`/`403` + startup
  key requirement, mapping draft/confirm through the gate, model endpoints.
- `tests/e2e/` — dataset-7 full pipeline persisted + byte-identical across two
  runs; API serves a persisted dataset-7 run; stop → confirm → retry yields a new
  identity (`X1 ≠ X0`, `X0.node1` absent, `X1` completes, `superseded_by = X1`,
  re-trigger reuses `X1`); CLI `--persist-run` + `gc`.

Full suite: **1130 passed, 1 live-LLM skipped**; `ruff check .` clean;
`mypy schemas` clean.

## Out of scope (documented)

SSE/webhooks; PDF rendering; multi-worker queues (Celery/RQ/Kafka); real RBAC/user
accounts; S3/Postgres backends; MLflow/model registry; on-demand re-scoring
(forbidden by D-P3).

## Deviations / notes

- The plan's `RunIndexEntry` is realized by the single `RunSummary` contract
  (index row + API representation) to avoid a duplicate schema.
- `finished_at` is operational and stored in the index; `runs/<id>/summary.json`
  omits it (the deterministic state/report outputs carry no wall-clock time).

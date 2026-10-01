# Codebase Review — churn_survival

- **Date:** 2026-09-30
- **Branch:** `feat/routing_and_datbase`, reviewed at `cde614d`. The uncommitted changes present at review start were committed as `cde614d` during the review, so the reviewed content is identical.
- **Method:** a read-only review with the ECC plugin. Seven reviewer agents each ran in their own context:
  - `python-reviewer` ×4, covering:
    - ingestion: `adapters/`, `router/`, `node1/`, `schemas/`, `config/`, `pipeline/`
    - `node2/` and `node3/`
    - `node4/` and `node5/`
    - `orchestration/`, `api/` and `scripts/`
  - `typescript-reviewer` for `frontend/` (vanilla ES modules)
  - `database-reviewer` for the SQLite run index and persistence
  - `security-reviewer` for the whole repo, against OWASP Top 10
- **Coverage:** `pytest --cov`, with its output redirected outside the repo.
- **Changes:** no source files were modified; REVIEW.md is the only file added.
- **Verification:** findings marked **✔ verified** were confirmed directly against the code or by execution. All others are agent findings from reading the code, and were cross-checked where several agents reported the same issue.

---

## 1. Architecture summary

A deterministic five-node pipeline turns heterogeneous customer data into a ranked, explainable churn-risk report. `architecture.md` is the authoritative specification, and `AGENTS.md` records the build status.

| Layer | Package | Role |
|---|---|---|
| Contracts | `schemas/` | Strict Pydantic v2 models (`extra="forbid"`) for every node boundary, with a central StrEnum vocabulary (`schemas/enums.py`). |
| Config | `config/` | Frozen, versioned config models, a JSON/YAML loader, and per-deployment configs (`config/node1/v*.json`, `node2..5`). Confirmed mappings live in `config/mappings/map_*.json`. |
| Node 1 (ingestion) | `adapters/`, `router/`, `node1/` | Header fingerprint → router → deterministic adapter (clean CSV, Excel, Stripe, HubSpot, Zendesk/Intercom, or a human-confirmed mapping adapter with whitelisted transforms) → validation gates → canonical records plus a quarantine list. The LLM only drafts mapping proposals, and a human confirms them. |
| Node 2 (survival) | `node2/` | Eligibility gates → CoxPH (penalized, delta-method CIs, bootstrap C-index, PH test with one stratified refit) or a Kaplan-Meier fallback → a content-addressed model artifact (`models/<version>/model.json` + `model.joblib`). |
| Node 3 (support signals) | `node3/` | Support threads plus external sources (X, Gmail; mock-first) → preprocessing (language, dedup, token budget) → per-thread LLM extraction with an offline keyword fallback → per-customer aggregation. |
| Node 4 (synthesis) | `node4/` | Deterministic scoring. Critical level comes only from explicit rules. Produces a total-order ranking and structured reasons. No LLM. |
| Node 5 (report) | `node5/` | Presentation only: copies Node 4 verbatim, looks up evidence, applies deterministic recommendations, and runs optional LLM explanation polish behind a strict validator with a template fallback. Renders JSON and HTML. |
| Orchestration | `orchestration/` | Plain-Python state machine: routing → Node 1..5, with stop states (`STOPPED_NEEDS_MAPPING`, `STOPPED_VALIDATION`). Content-addressed `run_id`, filesystem run store plus SQLite index, GC, and a human mapping gate. |
| API | `api/` | A FastAPI serving layer. Reads never recompute. `POST /runs` enqueues onto a single-worker executor. Uploads and mapping draft/confirm are write-gated (`API_ENABLE_WRITES` + `API_KEY`). |
| Frontend | `frontend/` | "Horizon", a dependency-free static UI served by FastAPI. Screens: upload → run status polling → mapping confirmation → report → history. |
| Tooling | `pipeline/`, `scripts/`, `logging_setup.py` | CLI (`churn-survival …`), dataset generators and evaluation harnesses, the reproducibility audit, and structlog to stderr. |

**Overall assessment:**
- The statistical core and the node contracts are solid and well defended. Node 4 and Node 5 honour their hard rules:
  - no Critical level from the combined score alone
  - no re-sorting
  - customer-bound evidence
  - HTML fully escaped
- There is no SQL injection and there are no committed secrets.
- The weakest area is the newer serving layer (Phase 8/9 plus the Horizon frontend). Its main problems are access control on reads, run-index and persistence integrity, and concurrency and atomicity around runs and mapping confirmation.

---

## 2. Findings by severity

The **Area** column names the reviewer that raised each finding: PY = python-reviewer, TS = typescript-reviewer, DB = database-reviewer, SEC = security-reviewer.

### CRITICAL

None found. No reviewer reported a remotely exploitable unauthenticated write, remote code execution path, or data-destroying bug reachable by default configuration. (Writes are off by default, and the API binds to `127.0.0.1`.)

---

### HIGH

| # | Area | Location | Issue | Failure scenario | Suggested fix |
|---|---|---|---|---|---|
| H1 | SEC / PY / TS ✔ verified | `api/deps.py:54-67`, `api/app.py:113-118`, all GET routes in `api/routes/{runs,uploads,models,configs}.py` | `require_auth` is attached only to POST routes. Every GET is unauthenticated even when `API_KEY` is set. This contradicts the `deps.py` docstring and `docs/phase8_persistence_api_plan.md:106` ("API_KEY set → every endpoint requires the key"). | An operator sets `API_KEY` and binds to a non-loopback host. Anyone can then read `/runs/{id}/report`, `/ranked-accounts`, `/node1..4` (per-customer churn scores) and `/raw-files/{name}` (full raw datasets). | Add a router-level or app-level `dependencies=[Depends(require_auth)]`, exempting `/health` and static assets. Keep "open when unset" inside `require_auth`. The frontend's `report.html` new-tab link will then need the key. |
| H2 | SEC | `api/routes/uploads.py:95-106, 135-146` | `GET /raw-files/{name}` returns the full content of any file in `RAW_DATA_DIR`, including customer CSV and Excel datasets. The route is meant for support JSON only. There is no kind restriction and no size cap, and `read_text` loads the whole file into memory. | Unauthenticated bulk exfiltration of every seeded or uploaded customer dataset. | Restrict the route to `kind == "support"` (`.json`), cap the size, and require auth. |
| H3 | Tests / CI ✔ verified (executed) | `config/mappings/map_20260927T181905Z.json` (committed in `cde614d`); `tests/dataset7/test_dataset7.py:148-153` | The suite is red: `test_router_german_csv_unmapped` fails with `DID NOT RAISE UnmappedFormatError`. The committed mapping (`confirmed_by: "api-key"`, `node1_config_version: "dataset7"`) has `headers_hash c02cb8ca…`, which is exactly the fingerprint of `data/raw/dataset7_customers_german.csv`, the router's negative fixture. Tests also read the live `config/mappings/` directory, so any mapping confirmed through the UI changes test outcomes. | CI (`.github/workflows/ci.yml`) fails. The German "must stay unmapped" guarantee is silently lost for every deployment that uses this repo's config directory. | Remove or relocate that mapping, since it looks like a manual UI test artifact. Point tests at an isolated `CONFIG_DIR` (tmp copy) so that UI or API confirmations cannot leak into test expectations. |
| H4 | PY ×2 / SEC ✔ verified | `router/llm_mapper.py:352-370` (`confirm_and_persist`), `adapters/mapping_adapter.py:322-339`, `api/routes/mappings.py:53-93` | Confirming a mapping has three problems: no duplicate-`headers_hash` check, a second-resolution filename (`map_%Y%m%dT%H%M%SZ`), and a non-atomic `write_text`. | (a) Confirming the same dataset twice (double-click, retry, or the frontend's non-retry-safe confirm, see M-FE2) writes a second file with the same fingerprint. After that, `load_confirmed_mapping_adapters` raises "duplicate mapping configs" on every `build_adapters` call, so all runs for all datasets fail with 422 until a file is deleted by hand. (b) Two confirms in the same second silently overwrite each other. (c) A crash mid-write leaves a corrupt config that also breaks every run. | Under a lock, reject or supersede an existing mapping with the same `headers_hash`. Create files exclusively (`open("x")`) with a uniqueness suffix. Write through a temp file plus `os.replace`. |
| H5 | DB / PY ✔ verified | `orchestration/persistence.py:205-210`, `orchestration/index.py:129-138`, `api/service.py:153-219`, `orchestration/index.py:162-164` | `RunStore.save()` upserts a summary rebuilt from `PipelineResult` using `INSERT OR REPLACE`. `build_summary` never sets `created_at`, `started_at` or `superseded_by`, so the RUNNING row written by `_enqueue` is replaced with NULLs for those fields. `summary.json` never contains them either. | Every completed run ends up with `created_at = NULL`. `GET /runs` orders by `COALESCE(created_at,'') DESC`, so finished runs sort by hash and `limit=50` can omit the newest runs. `superseded_by` lineage is lost on re-save, and a disk rebuild loses the fields permanently. | Use `INSERT … ON CONFLICT(run_id) DO UPDATE SET …` with `COALESCE` for the operational columns, or merge the existing row before saving. Write `summary.json` after the merge. |
| H6 | DB | `orchestration/persistence.py:236-249, 282-305` | `list_runs` calls `rebuild_index_from_disk()` whenever a filtered or limited query returns nothing. That rebuild overwrites live rows with the possibly stale `summary.json`. | `GET /runs?status=FAILED` with no FAILED runs rewrites the entire index from disk. INTERRUPTED rows, timestamps and `superseded_by` revert, and a RUNNING forced rerun can be overwritten with its old COMPLETED summary. A read endpoint therefore mutates state. | Self-heal only when the unfiltered row count is 0, or in an explicit startup or repair step. Rebuild with `INSERT OR IGNORE`, and skip corrupt `summary.json` files individually. |
| H7 | PY / DB ✔ verified | `api/service.py:214-219`, `api/routes/runs.py:162` | Only `run_pipeline` is inside the try block. A failure in `store.save()`, `render_html`, a disk write, SQLite, or `_merge` escapes the worker. The executor future is never inspected, so the exception is never logged. | The row stays RUNNING forever. Re-POSTing returns "in progress", and `force` returns 409. The run cannot be retried until a restart plus recovery. | Wrap post-run persistence so it records `FAILED` with `error_code=PERSIST_ERROR`. Add a `future.add_done_callback` that logs exceptions. |
| H8 | PY | `node3/llm_extractor.py:435-478, 533-540`, `node3/node.py:102-108` | The retry loop only catches `JSONDecodeError`, `ValidationError`, `ValueError` and `KeyError`. A malformed but valid-JSON payload raises `AttributeError` or `TypeError`, for example `{"risk_flags":["cancel"]}`, `"sentiment":"negative"`, or a top-level list. Client, HTTP and timeout errors are not caught at all. There is no per-thread guard. | A single bad LLM response among 5,000 threads kills the whole Node 3 run, and the orchestrated pipeline fails with `NODE_EXCEPTION`. This contradicts the §3.9 "quarantine the thread" contract. | Validate the payload with a Pydantic model or `isinstance` checks. Catch `AttributeError`, `TypeError` and client exceptions, and quarantine with `LLM_EXTRACTION_FAILED` and a redacted detail. |
| H9 | PY (executed by agent) | `adapters/util.py:48-63` (`parse_date`), `adapters/base.py:124-127`, `adapters/tenure.py` | `pd.NaT` is an instance of `datetime`, so `parse_date(NaT)` returns `NaT` rather than `None`. | Any Excel workbook with a blank date cell makes the adapter raise `TypeError: … 'datetime.date' and 'NaTType'` during tenure computation, and the whole batch aborts instead of quarantining the row. Every adapter shares this path. | Add `if value is None or pd.isna(value): return None` at the top of `parse_date`. |

---

### MEDIUM

#### Security

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-S1 | `orchestration/persistence.py:168-169, 221-262`, `api/routes/runs.py` (`{run_id}` routes), `api/routes/models.py:23-48` | `run_id` and `model_version` path parameters are joined straight into `base / value`. Starlette blocks `/` but not `\` (`%5C`), and Windows is the target platform. | `GET /runs/..%5C..%5Cx/report` or `/models/..%5C..%5Cx` reads `node5.json`, `report.html` or `model.json` outside the store (limited to those fixed filenames). Error messages echo the id back. | Validate against `^[0-9a-f]{16,64}$` in `RunStore.run_dir`, in `_sidecar_path` and in an API dependency. Also assert `resolve().is_relative_to(base)`. |
| M-S2 | `config/loader.py:48-97` via `api/service.py:113-114`, `api/schemas.py` (`RunTriggerRequest.node*_version`, `MappingConfirmRequest.node1_config_version`) | Config version strings are interpolated unvalidated into `…/v{version}.json`. `prepare_run` errors are echoed verbatim (`api/routes/runs.py:200-204`). | An authenticated writer can open any `*.json` on disk. Pydantic error text can leak fragments of those files and reveals whether files exist. A dangling `node1_config_version` stored on a mapping makes every later `auto` run fail with `FileNotFoundError`. | Add `Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")` and reject `..`. Check containment in the loader, check the config exists at confirm time, and return generic 422 messages. |
| M-S3 | `node2/artifact.py:99-103` | `joblib.load` (pickle) runs before the sidecar check, and the sidecar compares only the version string, with no hash. The function is not reachable from the API today. | Anyone able to write into `models/<v>/` (a shared volume, OneDrive sync, or a restored backup) gets code execution when the artifact is loaded. Combined with M-S1, a future API path to this function would become remote code execution. | Store a SHA-256 of `model.joblib` in `model.json` and verify it before loading. Confine the path and validate the version format. Longer term, persist params as JSON. |
| M-S4 | `api/routes/uploads.py:149-193`, `api/schemas.py:31`, `api/routes/runs.py:173` | The 200 MiB upload cap is checked only after Starlette has spooled the whole body. `support_data: list[dict]` has no size, item-count or depth limit. The executor queue is unbounded, and there is no rate limiting. The `/mappings/draft?use_llm=true` endpoint has no quota. | A key holder, or anyone if the key leaks, can exhaust disk, memory, CPU or LLM spend. | Add a body-size middleware or reverse proxy limit, `max_length` on `support_data`, a bounded queue, rate limiting, and an LLM draft quota. |
| M-S5 | `api/deps.py:54-72`, `api/routes/mappings.py:73` | `secrets.compare_digest` on non-ASCII `str` raises `TypeError`, giving a 500 instead of a 401. `X-Actor` and `confirmed_by` are fully client-controlled yet recorded as the audit approver. There is a single shared key and no brute-force throttling. | Spoofed approver identity in mapping provenance, and 500s on malformed headers. | Compare `.encode()` bytes. Record a key fingerprint or authenticated identity rather than trusting `X-Actor`. Throttle and log auth failures. |
| M-S6 | `api/app.py` (no middleware), `api/routes/runs.py:88-95` | There are no security headers: no CSP, `nosniff`, `frame-ancestors` or `Referrer-Policy`. `report.html` is served same-origin with the SPA, which keeps the API key in `localStorage`. | Any future escaping slip in the report, which carries LLM-derived text, becomes stored XSS that can read the key. | Add a headers middleware with a strict CSP, and serve `report.html` with `Content-Security-Policy: sandbox`. |
| M-S7 | `api/deps.py:76-89`, `api/routes/uploads.py:116-131`, `api/routes/mappings.py:47-50, 86-89`, `api/routes/uploads.py:179` | Information disclosure. `is_file()` runs before the confinement check, creating a file-existence oracle. `/raw-files` returns absolute server paths. Raw `str(exc)` text (pandas and Pydantic internals, input values) is echoed in responses. | An attacker can probe the filesystem layout and read internal error detail. | Check confinement first, return relative names only, and send generic messages while logging the detail. |

#### Persistence, orchestration and API correctness

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-O1 | `api/routes/runs.py:135-164, 206-247`, `api/service.py:145-150` | POST `/runs` does check-then-enqueue (`get_summary`, then `upsert`, then `submit`) and `_merge` does a read-modify-write. Neither is atomic, and both run in the sync threadpool concurrently with the worker. | Two identical concurrent POSTs both enqueue, so the same `runs/<id>/` is computed and written twice. `_merge` can lose a concurrent `superseded_by` update. | Claim the run with an atomic insert-if-absent (`ON CONFLICT DO NOTHING` plus a `rowcount` check) or a lock. Use single-statement `UPDATE`s. |
| M-O2 | `orchestration/persistence.py:175-211`, `orchestration/state.py:177`, `router/llm_mapper.py:361` | Files are written non-atomically, and `summary.json` is written last. Stale outputs are never removed. | A read during a forced rerun hits truncated JSON and returns 500. A rerun that ends STOPPED or FAILED keeps the old `node5.json` and `report.html`, so the API serves outputs that contradict the row's status. | Write to a temp file and `os.replace`, or swap in a staged directory. Delete outputs the new result lacks. |
| M-O3 | `orchestration/index.py:29-60, 111-116` | There is no schema versioning or migration (`CREATE TABLE IF NOT EXISTS` only, no `user_version`). | Adding a `RunSummary` field makes every `upsert` on an existing DB fail with "no column named X". Combined with H7, runs then hang in RUNNING. | Add `PRAGMA user_version` with `ALTER TABLE` migrations, or treat the index as a cache and drop and rebuild it on a version mismatch. |
| M-O4 | `orchestration/gc.py:63-78, 98-136`, `api/app.py:41` | GC ranks runs by directory mtime, which in-place overwrites do not update. It ignores status (RUNNING directories can be pruned), uses a non-recursive `unlink`, and prunes models still referenced by kept runs. `--recover` and startup recovery do a global RUNNING→INTERRUPTED update. | A forced rerun looks old and is deleted mid-run. Recovery from a second process or uvicorn worker marks live runs INTERRUPTED. | Order by `summary.finished_at`, skip RUNNING and PENDING runs, use `shutil.rmtree`, and protect referenced models. Record a run owner and heartbeat, or enforce a single worker with a lock file. |
| M-O5 | `orchestration/gc.py:108-129`, `orchestration/persistence.py:264-271` | Orphan index rows (runs that never wrote a directory) are never collected. `delete` removes files before the index row. | Ghost FAILED or INTERRUPTED rows accumulate in `/runs` forever. | Drive GC off the index as well, and delete the index row first or reconcile rows against disk. |
| M-O6 | `api/service.py:174-188`, `orchestration/identity.py`, `scripts/audit_reproducibility.py:199-214` | The LLM provider and model are not part of `run_id`, and the audit reruns without an LLM. | LLM and template runs share one id, so a cached result is returned regardless of provider. The audit reports a false `FAIL` on `node5.json`/`report.html` for LLM runs. | Fold the LLM provider and model into `config_versions`, or record LLM use and skip or replicate it in the audit. |
| M-O7 | `api/routes/uploads.py:149-193` | The async upload handler does blocking file I/O and writes directly to the final path. On 413 or other errors it `unlink`s, which deletes the file that was there before. | The event loop stalls on large uploads. A re-upload truncates a file that a run is reading. A failed re-upload destroys the previous dataset. | Stream to a temp file and `os.replace` on success. Use a sync `def` or `run_in_threadpool`. Version or reject name collisions. |
| M-O8 | `orchestration/routing.py:88-96` vs `59-63` | `resolve_node1_version` routes with `high_confidence_threshold=0.0`, while the real run uses the config threshold. | Auto-resolution can pick a config for a different adapter than the one that actually transforms the data, or resolve a config the run then treats as unmatched. | Use the same threshold, or reuse a single routing decision. |

#### Ingestion (Node 1 / adapters / router)

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-I1 | `router/llm_mapper.py:338-345` | Only `llm_model_used` is overwritten from the client. `source_fingerprint` (whose `headers_hash` routes future files) is taken from the LLM output, and only its length is checked. A non-dict payload raises an uncaught `TypeError`. | The LLM mangles one hex digit, and the confirmed mapping never matches its dataset, or matches a different one. | Set `payload["source_fingerprint"] = fingerprint.model_dump(mode="json")` and check `isinstance(payload, dict)`. |
| M-I2 | `adapters/mapping_adapter.py:73-84, 222-226` | `is_allowed_transformation` catches only `ValueError`, but `ast.literal_eval` raises `SyntaxError`, `TypeError` or `RecursionError` (reproduced by the agent). | A malformed `map(...)` escapes `validate_mapping_report` as an unrelated exception rather than `MappingReportError`. | Catch all of these and re-raise them as `ValueError`. |
| M-I3 | `adapters/_table.py:41-42`, `adapters/mapping_adapter.py:281-287` | `iterrows` upcasts ints to `float64` on all-numeric frames (reproduced by the agent). | An integer `customer_id` becomes the string `"1.0"`, which silently breaks the join with Node 3 support data. | Iterate with `astype(object).itertuples()` or `to_dict("records")`. |
| M-I4 | `adapters/hubspot_crm.py:55-57, 62-66`, `adapters/stripe_customers.py:71` | `a or b` fallbacks never fire on blanks because NaN is truthy. Unknown `lifecyclestage` values are silently mapped to `event=0`. | A blank email with an `hs_object_id` present is rejected instead of falling back. Unknown or misspelled churn stages enter the survival model as censored, biasing hazards downward. | Add a `first_present()` helper based on `pd.isna`, and leave unknown stages as `None` so Gate 6 quarantines them. |
| M-I5 | `node1/feature_gate.py:123-149, 188-194, 210-241` | After dropping `None`s, lists are paired by position, so rows misalign. NaN is not treated as missing. | Multicollinearity warnings are spurious or never fire for extras with blanks, and promotion stats are wrong. | Pair values by record index where both are finite, using a shared `_is_missing`. |
| M-I6 | `config/models.py:320-343` | `Node1Config` does not validate `approved_core_keys` against the `CoreFeatures` union, nor require a `core_key_types` entry for each approved key. A missing entry silently defaults to `"string"`. | A typo'd key passes Gate 8, then `CanonicalRecord` raises `RuntimeError` for the whole batch. A numeric key with no declared type rejects every record. | Add a `@model_validator`. |
| M-I7 | `node1/validation.py:99-108, 156-161, 283-299` | Tenure sanity is computed over all records, including already-rejected ones. This is the same class of bug as QA finding F-2. | Invalid rows can trip the outlier or zero-fraction gate and fail a batch that is otherwise healthy. | Compute it only over the evaluable subset. |

#### Node 2 / Node 3

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-N1 | `node2/assumptions.py:83-110` | When the worst PH violator is numeric, the code still stratifies on `categorical_specs[0]`, reports `decision="stratify"`, and never re-tests PH. | The Dataset 7 case: the violation on `support_tickets_90d` persists but is reported as handled. | Stratify only on a categorical violator; otherwise fall back or warn. Re-run the PH test after the refit. |
| M-N2 | `node2/cox.py:47-89` | On a stratified refit, the strata variable's dummy columns stay in as covariates. | Unidentifiable coefficients, with meaningless HRs, CIs and interpretations, and possible singular-Hessian warnings. | Drop the strata dummies from the fit and prediction frames. |
| M-N3 | `node2/cox.py:116-184` | `survival_ci` uses unweighted pooled Nelson-Aalen variance, which ignores `exp(xβ)`, the β covariance and strata. The empty `event_times` case returns `{}`, and `_score` then raises `KeyError`. | Mis-calibrated "95% CI" values, and a crash on degenerate data. | Use the Breslow variance per stratum, or lifelines' own CIs. Guard the empty case and label the interval approximate. |
| M-N4 | `node2/assumptions.py:113-138` | The bootstrap C-index is computed in-sample on resamples of the training data, and mixes strata. | `validation_metrics.c_index` overstates out-of-sample discrimination. | Use an optimism-corrected bootstrap with within-stratum concordance. |
| M-N5 | `node2/artifact.py:106-139`, `node2/node.py:292` | `training_timestamp` defaults to `datetime.now(UTC)` on the CLI path, which violates hard rule 1. | Artifact bytes differ between identical runs. | Default it to midnight UTC of `reference_date`. |
| M-N6 | `node3/node.py:100-108`, `node3/preprocess.py:457-461` | LLM extraction is called for collapsed duplicates and for customers outside the requested universe. | Wasted LLM cost and latency, and customer text sent to the LLM needlessly. | Skip threads with `duplicate_of` set, and filter to `requested` customers before extraction. |
| M-N7 | `node3/aggregate.py:232-246` | Message volume and support status are summed over failed and quarantined threads. | One usable thread plus a large unsupported-language thread yields `SUFFICIENT_DATA` with an inflated confidence. | Compute these from `usable` threads only. |
| M-N8 | `node2/kaplan_meier.py:102-123` | `iterrows` with per-row `.loc` lookups, per horizon. | The 71k-row Cell2Cell KM fallback takes minutes. | Vectorise per segment. |

#### Node 4 / Node 5

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-R1 | `node5/report/transformer.py:171-176, 197` | The template headline uses `primary_reasons[0]`, and Node 4 orders reasons by category rather than importance. | A High account with a strong support signal gets the headline "High — no quantitative risk score". This is the default path whenever the LLM is off or rejected. | Choose the headline by severity and demote `MISSING_*`/`LIMITED_*` reasons. |
| M-R2 | `node5/report/explanation_validator.py:165, 242-250` | `integers={90}` is only consulted for spelled-out numbers, so a digit token "90" is always rejected. | "90-day survival probability" (the template's own wording) triggers `UNSUPPORTED_NUMBER`, causing retries and a fallback. | Accept digit tokens that are in `allowed.integers`. |
| M-R3 | `node5/report/explanation_validator.py:64-89, 273-280` | The unsupported-risk-factor check is a narrow phrase list that misses pricing, frustration and switching language. | An LLM claims "pricing dissatisfaction" for an account with only `usage_drop`, and the text is published as `llm`. | Broaden the lexicon, or move to an allow-list of template-derived phrasing. |

#### Frontend (Horizon)

| # | Location | Issue | Scenario | Fix |
|---|---|---|---|---|
| M-FE1 | `frontend/static/views/runStatus.js:104-113`, `app.js:53-56` | An INTERRUPTED-run resubmit returns the same `run_id`, so the hash is unchanged, no `hashchange` fires, and polling never restarts. (The agent rated this HIGH; it is graded MEDIUM here because INTERRUPTED is rare.) | The UI appears frozen until a manual reload. | If the returned id equals the current one, re-render the view directly. |
| M-FE2 | `frontend/static/views/mapping.js:288-304` | Confirm and trigger are two calls. If the trigger fails, the button re-enables and a second click re-confirms. | This feeds straight into H4 (a duplicate mapping bricks all runs). | Track the confirmed state and retry only `triggerRun`. |
| M-FE3 | `runStatus.js:107`, `mapping.js:296-298` | Resubmit and re-trigger send only `raw_path`, dropping `support_data` and any `node1_version` override. | Post-mapping runs silently lose Node 3 data and get a different `run_id`. | Carry the original trigger spec forward, or have the API return it. |
| M-FE4 | `frontend/static/views/history.js:9, 29-59` | The "Completed" filter is never applied, and filter responses race (last response wins). | The filter shows the wrong runs. | Pass `status` to the API and guard with a request token or `AbortController`. |
| M-FE5 | `frontend/static/views/report.js:163-283` | The drawer is appended to `document.body` with no teardown. | A stale account drawer stays over other views after navigation. | `ctx.onTeardown(closeDrawer)`, and close on Escape. |
| M-FE6 | `runStatus.js:137-154` | Polling stops permanently on any single error. | One network blip shows "Could not load run" while the run continues. | Retry with backoff on network errors, 5xx and 429. |
| M-FE7 | `report.js:121-125, 165`, `history.js:114-127` | Clickable `<tr>` rows are mouse-only, and the drawer is not an accessible dialog. | Keyboard and screen-reader users cannot open runs or accounts. | Use real links or `tabindex`/`role`/key handlers, and `role="dialog"` with focus management. |

---

### LOW

**Security and serving**
- **L1 — `api/routes/uploads.py:160-164`:** an upload silently overwrites an existing dataset of the same name, concurrent uploads interleave, and a partial file is visible mid-upload.
- **L2 — `api/routes/configs.py:33-42`:**
  - One malformed `v*.json` makes `GET /node1-configs` return 500, which breaks the UI picker.
  - The route also loads via the global `config_dir()` instead of the globbed `node1_dir`.
- **L3 — `api/routes/runs.py:134-160`, `api/app.py:36`:**
  - Queued runs are shown as RUNNING, with `started_at` set at enqueue time.
  - `shutdown(cancel_futures=True)` leaves them RUNNING.
  - Fix: add a real PENDING state.
- **L4 — `api/routes/runs.py:155-160`, `api/service.py:217-219`:** `supersedes_run_id` may equal its own id or an unknown run. When `actual_id != run_id`, `previous.superseded_by` dangles.
- **L5 — `orchestration/index.py:162-164`:** there are no secondary indexes; sorting on the `COALESCE` expression forces a full scan and sort.
- **L6 — `orchestration/index.py:225-260`, `api/routes/runs.py:57-65`:**
  - One undecodable row returns 500 for the whole `/runs` listing.
  - `total` is the page size, not the match count, and there is no offset or cursor beyond `limit` (max 1000).
- **L7 — `pyproject.toml`:**
  - Dependencies are unpinned: no floors for `python-multipart`, `starlette` or `fastapi`, all of which have DoS CVE history.
  - CI does not run `pip-audit`.
- **L8 — `router/llm_mapper.py:55-83`:** `LlmClient.base_url` is taken from env only and carries the API key. Keep it env-only and https-validated, since it would become an SSRF and key-exfiltration path if it were ever exposed via the API.
- **L9 — `frontend/static/api.js:5-22`:** the API key is stored in `localStorage`. Prefer `sessionStorage` or in-memory storage, together with a CSP (M-S6).
- **L10 — `frontend/static/components/ui.js:9`:** the `html:` → `innerHTML` path in `el()` is unused but is a latent XSS footgun. Remove it.

**Ingestion**
- **L11 — `node1/validation.py:248-257` vs `201-208`:** empty or whitespace strings are accepted by Gate 8 but counted as missing by the missingness gate.
- **L12 — `node1/node.py:87, 167`, `adapters/base.py:129`:** the Node 1 CLI's `ingested_at` and the draft's `generated_at` use wall-clock time, so they are non-deterministic.
- **L13 — `adapters/util.py:15-23, 48-63`:** `parse_date` accepts trailing garbage (`"2026-08-01garbage"`), and `%m/%d/%Y` silently wins over DD/MM.
- **L14 — `adapters/util.py:66-80`:** `to_float("1,5")` returns `15.0`, a silent 10× error for European decimals.
- **L15 — `adapters/mapping_adapter.py:213-221`:** a NaN cell matches a `"nan"` key in `map({...})`.
- **L16 — `router/llm_mapper.py:286-315`:** `validate_mapping_report` has no check for duplicate `target_field`s, for source columns missing from the fingerprint, or for identity fields that are not mapped.
- **L17 — `adapters/stripe_customers.py:19-26`:** a weekly interval maps to a 1-month contract length.
- **L18 — `node1/feature_gate.py:23-60`, `node1/validation.py:286-295`:** dead code: `split_features`, `evaluate_promotion`, and the unreachable Gate 10 `NON_FINITE` branch.
- **L19 — `pipeline/main.py:26-47`, `logging_setup.py:106-112`:** unreachable "not implemented" branches, and `emit_node_completion` swallows all exceptions silently.

**Node 3**
- **L20 — `node3/preprocess.py:143-161`:** language detection does not strip punctuation, so short English messages become UNKNOWN, and accents can misclassify English as unsupported.
- **L21 — `node3/preprocess.py:388-395`:** `language` codes are not normalised, so `"EN"`, `"en-US"` and `"en_GB"` are quarantined as unsupported.
- **L22 — `node3/llm_extractor.py:136, 157-158`, `node3/aggregate.py`:** offline keyword rules match substrings with no negation handling, for example `"bug"`→`debug` and "won't cancel"→cancel.
- **L23 — `node3/node.py:347, 441-443`:**
  - List membership is O(n²) on large universes.
  - Raw exception text, which can include LLM input values, is persisted in `error.detail`.

**Node 4 / Node 5**
- **L24 — `node5/report/explanation_validator.py:151-155, 306-310`:**
  - Multi-word actions ("reach out") can never be allowed.
  - Substring matching over-rejects ("provided"→"provide").
- **L25 — `node5/report/explanation_validator.py:198, 242-270`:** the display name and `customer_id` are not masked before checks. "High Street Bakery" or "CUST-0771" therefore causes a permanent template fallback.
- **L26 — `node4/node.py:244-248, 371`, `node5/report/evidence.py:75-91`:** model-wide top drivers are shown as per-account evidence.
- **L27 — `node4/evidence.py:41-50`:** evidence refs include `positive_feedback` messages and flags that Node 4 discarded as `INCONSISTENT_SUPPORT_STATUS`.
- **L28 — `node5/report/evidence.py:186-191`:** the Node 2 placeholder always takes the first evidence slot, so with `max_items=1` no quotes are shown.
- **L29 — `node5/report/deterministic_sections.py:39-48`:** the executive summary calls Low accounts "priority accounts" and ignores the `max_accounts_in_summary` truncation.
- **L30 — `node5/llm/explainer.py:103-119`:** retries resend the identical prompt, and `llm_failures` counts attempts rather than accounts.
- **L31 — `node5/report/consistency.py:16-23`:**
  - Node 5 requires both upstream versions, so a documented §4.14 single-upstream Node 4 output cannot be published via the CLI.
  - `evidence_ref.message_ids` are copied even when evidence is disabled (D-U6 inconsistency).

**Frontend**
- **L32 — `frontend/static/api.js:43-74`, `mapping.js:71-138, 307`:** leftover `console.log` calls log full run summaries, and a duplicate click listener is registered on `draftBtn`.
- **L33 — `app.js:55`, `history.js:119-123`, `runStatus.js:69,74`:** the `run_id` is not encoded into the hash and not decoded in `parseHash`. Latent only, since ids are hex.
- **L34 — `mapping.js:18, 162-171`:** the placeholder `map({...})` transform is selectable and is invalid.
- **L35 — `mapping.js:219-225`:** the core-key datalist hardcodes the `CoreFeatures` union.
- **L36 — `history.js:108-110`:** the lineage label always reads "stopped → completed", whatever the successor's status.
- **L37 — `app.js:116`:** a synchronous render throw is uncaught.
- **L38 — `models.js:28-34`:** model details are fetched sequentially (N+1); use `Promise.all`.
- **L39 — `api.js:159`:** the 401 copy says "sidebar", but the key field is in the top bar.

**Scripts**
- **L40 — `scripts/generate_dataset7.py:1432-1439`:** the generator stages its output in `$TEMP` and then calls `os.replace`, which fails across drives.

---

## 3. Test coverage

**Run:** `pytest --cov=.` on Windows, Python 3.12.13. Result: **1174 passed, 1 failed, 1 skipped** (the live-LLM test) in 9m 11s. **Total coverage is 95%** (6952 statements, 246 missed; 1984 branches, 181 partial). 61 files have 100% coverage.

**Failure:**
- `tests/dataset7/test_dataset7.py::test_router_german_csv_unmapped` (see **H3**).
- Also noteworthy: 336 `DeprecationWarning`s from joblib under NumPy 2.5 (`array.shape = …`) in `tests/node2/test_artifact.py` and `test_flow.py`. Pin joblib or upgrade it before NumPy removes the behaviour.

### Modules below 93%

| Module | Cover | Untested lines / area |
|---|---|---|
| `api/main.py` | **0%** | 6-24: the whole uvicorn entry point (`churn-survival-api`), including the settings-to-startup path. |
| `pipeline/main.py` | 78% | 32-34, 47-48, 70-79, 87-89, 106-117: CLI dispatch error branches, unknown subcommands, the `gc` and `audit` argument paths. |
| `api/routes/uploads.py` | 84% | 65, 71, 76, 102, 118: extension and kind classification edges. 168, 173-178: the **200 MiB size-cap rejection and cleanup path** (never exercised, which is also where M-O7's "deletes previous file" bug lives). |
| `node2/horizons.py` | 85% | 26, 30, 43: horizon-unavailable branches. |
| `orchestration/persistence.py` | 86% | 228-233, 244, 276, 285, 302, 309: `list_runs` self-heal and rebuild branches (H6), plus corrupt or legacy summary handling. |
| `orchestration/gc.py` | 87% | 44, 55, 59-60, 115, 145, 152, 194-196, 206: TTL edge cases, the model-pruning branches, and error paths. |
| `api/deps.py` | 88% | 60→67, 79, 83→90, 86: the **auth-failure branch** and **raw-path confinement rejection** (`raw_path` outside `RAW_DATA_DIR`). |
| `api/routes/mappings.py` | 88% | 68-71: confirm-by-`raw_path` fingerprinting. 77: the "mapping declined" 409. |
| `node5/validation/node4_validator.py` | 89% | 50, 60, 80, 95-99: several malformed-Node-4 rejection branches. |
| `api/app.py` | 90% | 35→exit, 43, 87-88, 99-101: lifespan shutdown, the startup-recovery path, the no-frontend mount branch. |
| `node1/node.py` | 90% | 198-207, 222-224, 228: the new `map --confirm --node1-config` CLI branch. 361-367, 382: the onboarding guide and error output. |
| `node5/report/consistency.py`, `reason_text.py` | 90% | Consistency-violation branches (91-92, 103, 114, 149-151, 166). |
| `node2/cox.py` | 92% | 54, 211-215: the strata-column handling (M-N2) and CI edge paths (M-N3). |
| `node2/eligibility.py`, `node3/node.py`, `node3/sources/registry.py`, `node3/sources/x_source.py` | 91-92% | Eligibility edge branches. Node 3 CLI and universe error paths (including 451-456 and 560). X live-mode credential branches. |

### Behavioural gaps (not visible in line coverage)

The review findings expose these untested behaviours, each worth a regression test:
1. **Read endpoints with `API_KEY` set** (H1). No test asserts that a GET without the key returns 401.
2. **Confirming the same mapping twice**, or two confirms in the same second (H4).
3. **`created_at`, `started_at` and `superseded_by` surviving `store.save()`**, and `/runs` ordering after completion (H5).
4. **A read-only `GET /runs?status=X` must not mutate the index** (H6).
5. **Failure inside `store.save()`** leaving the run RUNNING (H7).
6. **Node 3 with a malformed but valid-JSON LLM payload** (a list, or strings in place of objects) and client exceptions (H8).
7. **Excel input with blank date cells (NaT)** (H9), and an all-numeric CSV with an integer `customer_id` (M-I3).
8. **Windows backslash traversal** on `/runs/{id}` and `/models/{v}` (M-S1), and path-like config version strings (M-S2).
9. **Concurrent identical `POST /runs`** (M-O1), and GC while a run is RUNNING (M-O4).
10. **Test isolation from the live `config/mappings/`** (H3). Tests should run against a temporary `CONFIG_DIR`.
11. **Frontend:** there is **no JavaScript test harness**. `tests/api/test_frontend_static.py` (9 tests) only asserts static file contents. Views, polling, the router and the mapping confirm/retry flow have no behavioural tests. A minimal harness (Vitest with jsdom, or Playwright against the served app) would cover M-FE1 through M-FE6.
12. **The LLM path of the Node 5 explanation validator** with realistic phrasings, such as "90-day", pricing claims, and names containing risk words or digits (M-R2, M-R3, L25).

---

## 4. Suggested priority order

1. **H3:** remove the stray German mapping and isolate tests from the live `config/mappings/`, so CI is green again.
2. **H1, H2:** authenticate reads and restrict `/raw-files/{name}`.
3. **H4 and M-FE2:** make mapping confirmation idempotent and atomic.
4. **H5, H6, H7, M-O1, M-O2:** fix run-index and persistence integrity (upsert semantics, no mutation on reads, guarded persistence, atomic claim and writes).
5. **H8, H9:** harden Node 3 LLM payload handling and fix `parse_date` for NaT.
6. **M-S1, M-S2:** validate the formats of ids and version strings.
7. The remaining MEDIUM items by area, then LOW items opportunistically.

---

## 5. Addendum — LLM latency in Node 3 and Node 5 is the dominant cost (P1)

*Recorded after the review above. Node 1 and the frontend run-progress display
are resolved; with progress now visible, runs are confirmed to spend their time
in Node 3 (support signals) and Node 5 (report). This is the top remaining
operational blocker and is not a first-class finding above.*

**Symptom.** Runs stall for minutes to hours and appear to stop at Node 3 and
Node 5.

**Cause.** The live LLM (`LLM_PROVIDER=openai`) is wired into the API worker and
is called synchronously, once per item, so cost scales with rows rather than
with anything that changes the decision:

- **Node 3** — one LLM call per support thread, sequentially. On the review
  machine one call is ~0.75–1.3 s. `data/raw/dataset7_support_threads_messy.json`
  has **5,730 threads** → **~2 hours per run**. No batching, no concurrency.
- **Node 5** — up to `llm_max_retries + 1 = 2` calls per priority account.
  Runs `62df46c99c5bf8be` and `fda05b48079b954b` both recorded
  `llm_calls=100, llm_failures=100, explanation_source={llm:0, template:...}` —
  **every call was rejected and fell back to a template**, so the latency bought
  nothing.
- `config/node5/vdataset7.json` sets `max_accounts_in_summary: 10000` (v1 = `50`).
  With dataset7's 4,055 ranked accounts that is **~8,110 failing calls**.
- `RUN_MAX_WORKERS=1` compounds it: a long run holds the only worker, so later
  runs wait in `PENDING` (observed: 19 minutes) rather than starting.

**Why every Node 5 explanation is rejected.** `build_allowed_facts`
(`node5/report/explanation_validator.py:158`) never permits the account's own
`customer_id` / `display_name` digits (see L25), and `integers={90}` is only
consulted for spelled-out numbers, not digit tokens (M-R2). The model echoes the
customer number (`CUST-3242` → `3242`) and the validator returns
`UNSUPPORTED_NUMBER`.

**Suggested fixes (any subset removes the blocker).**
1. Do not run Node 5 LLM polish unless explicitly enabled; bound it with a
   dedicated `llm_max_accounts`, `llm_max_retries=0`, and a stop-after-N-failures
   circuit breaker; collapse the per-account failure warnings.
2. Fix `build_allowed_facts` to allow the account's own id/name digits, and accept
   digit tokens in `allowed.integers`.
3. Run Node 3 (and Node 5, if kept) extraction through a bounded
   `ThreadPoolExecutor` with a configurable `llm_max_concurrency`; per-thread
   outputs stay contract-identical.
4. Raise/decouple `RUN_MAX_WORKERS` so one long run cannot block every later run.

**Measured reference (review machine).** Node 1 alone, 71k rows: 2.3 s. Full
pipeline without support threads: 3–16 s. Node 3 offline (full 5,730-thread
corpus): 3.5 s. One LLM call: ~0.75 s. Node 5, 3 accounts with the LLM: 9.8 s
(6 calls, 6 failures).

**Status — resolved (2026-09-30).** All four suggested fixes applied:
1. `Node5Config` gained `llm_enabled` (default **false**; shipped `v1`/`vdataset7`
   keep it off), `llm_max_accounts` (25), and `llm_max_consecutive_failures` (3,
   circuit breaker; a success resets it). Shipped configs set `llm_max_retries: 0`.
   Rejections are collapsed into one run-level warning (plus one breaker/cap
   notice). `llm_model_version` is recorded only when the LLM is actually used.
   Implemented in `node5/node.py` (`_LlmBudget`); decisions are untouched.
2. `explanation_validator`: digit tokens in `allowed.integers` are accepted
   ("90-day", M-R2); the account's own `customer_id`/`display_name` are masked
   (whole-word, case-insensitive) before checks (L25); the lexicon now covers
   pricing (→ billing flag), switching/alternative-provider (→ competitor flag)
   and generic dissatisfaction (→ needs any risk flag) (M-R3).
3. Node 3 extraction runs through a bounded `ThreadPoolExecutor`
   (`Node3Config.llm_max_concurrency`, default 8) when an LLM client is present;
   `map` preserves input order, so output is byte-identical to the sequential
   path (regression test compares both).
4. `RUN_MAX_WORKERS` default 1 → 2 (bounded 1–16). **Note:** a local `.env` that
   still sets `RUN_MAX_WORKERS=1` overrides this.

Follow-up (same day): Node 5 explanations are also concurrent (ordered batches,
`Node5Config.llm_max_concurrency` = 4); `LlmClient` reuses one pooled
`httpx.Client` and retries 429/5xx/transport errors with `Retry-After`/backoff;
`NODE3_LLM_MAX_CONCURRENCY` / `NODE5_LLM_MAX_CONCURRENCY` env overrides (not part
of `run_id`).

Tests: `tests/node5/test_llm_budget.py`, `tests/node3/test_llm_concurrency.py`,
`tests/router/test_llm_client.py`, `tests/orchestration/test_concurrency_overrides.py`.
Also updated two stale Node 2 tests to the already-applied Node 2 fixes
(unresolved stratified refit → `fallback`; `survival_ci` with no events → all-NaN
per horizon instead of `{}`).

---

## 6. Second review — uncommitted diff on top of `cde614d` (2026-09-30)

- **Scope:** everything uncommitted on `feat/routing_and_datbase` after `cde614d`. This includes the remediation of §2–§5, the route-once and performance work, the LLM latency work, and the Horizon UI refresh (report search, filter, paging and CSV export; mapping checklist; `Cache-Control: no-cache` on the static mount).
- **Method:** read-only, with the ECC plugin. Nine reviewer agents ran in parallel, each in its own context. The **Area** codes below name them:

  | Code | Agent | Scope |
  |---|---|---|
  | CR | `code-reviewer` | whole diff |
  | PY | `python-reviewer` | Python diff |
  | API | `fastapi-reviewer` | `api/` |
  | SEC | `security-reviewer` | whole repo |
  | ML | `mle-reviewer` | Node 1/2/3/5, adapters, router, orchestration |
  | TS | `typescript-reviewer` | `frontend/static` |
  | A11Y | `a11y-architect` | WCAG 2.2 AA, frontend |
  | SF | `silent-failure-hunter` | whole diff |
  | TA | `pr-test-analyzer` | test gaps; ran the full suite |

- **Verification:**
  - **✔ verified** means confirmed directly against the code or by execution in the main session.
  - Findings reported by several agents were merged, and the count of agents is shown.
- **Test status:**
  - `LLM_PROVIDER=none uv run pytest` gives **1250 passed, 1 skipped, 0 failed**.
  - A plain `uv run pytest` gives about 580 failures and errors, because the local `.env` sets an LLM provider with no key (see T1).
  - `ruff check .` and `mypy schemas` are clean.
  - Frontend: a 23-check Playwright click-through in Edge (report, mapping, models) passes.

**Status of earlier findings touched by this review:**
- **H1 (reads unauthenticated):** fixed. Every router except `/health` now has `dependencies=[require_auth]` (`api/app.py:164-169`) ✔. Side effect: see N-H9.
- **H3 (suite red on the German fixture):** fixed. The mapping file is staged as deleted and `test_router_german_csv_unmapped` passes.
- **H4 (duplicate mapping confirm):** partly fixed. A duplicate `headers_hash` check and an exclusive `open("x")` are in place. A check-then-write race remains (see N-H7).

### HIGH

| # | Area | Location | Issue | Failure scenario | Suggested fix |
|---|---|---|---|---|---|
| N-H1 | ML ✔ verified | `node2/artifact.py:55-76` (`derive_model_version`); `node2/assumptions.py:163-190`; `node2/cox.py` | Node 2 fitting semantics changed, but `modeling_version` was not bumped, and the version hash includes only config version strings. Three changes: an accepted stratified refit must now pass a PH re-test, otherwise the run falls back to Kaplan-Meier; the strata variable's dummy columns are dropped; `prediction_frame` now uses `cph.params_`. | The same data and config yield the same `model_version` but a different model. `save_artifact` overwrites `models/<v>/` with different coefficients or `model_type`, which breaks the per-version reproducibility rule in `AGENTS.md`. | Bump `modeling_version` (or add a `FIT_ALGORITHM_VERSION` to the hash). Pin a stratified-fit fixture's coefficients in a test. |
| N-H2 | ML | `node2/assumptions.py:118-143` (line 130), used at 175-183 | The C-index is not a valid evaluation, for two reasons. For stratified fits it ranks `exp(x·β)` across strata with different baselines, while the shipped score is `1 − S(t_ref)`, which uses the per-stratum baseline. The bootstrap also resamples in-sample predictions, so it measures apparent (training) performance, not held-out. | The refit gate and `validation_metrics.c_index` rest on an inflated or deflated number that does not describe the delivered score. | Compute concordance on `1 − S(t_ref)`, or within strata. Label the metric as apparent C-index, or add a held-out or time-split C-index. Record the metric kind. |
| N-H3 | ML / PY / CR | config: `config/node5/v1.json`, `vdataset7.json`. Code: `node3/aggregate.py:231-261`, `node3/llm_extractor.py`, `adapters/util.py:69-120`, `adapters/_table.py:35-47`, `node1/validation.py` | Output-changing edits to frozen nodes and configs have no version bump. Run identity hashes version strings only. Four changes: (1) Node 5 `v1` gains `llm_enabled`, the budget fields and `llm_max_retries 1→0`. (2) Node 3 limited-data status uses usable messages, and the offline extractor now handles negation and word boundaries. (3) `parse_date` no longer truncates `"2026-01-01 UTC"`, and `to_float("1,5")` returns `None` instead of 15. (4) An all-numeric CSV now yields `customer_id "1"` instead of `"1.0"`. | Same raw file plus same version strings gives the same `run_id`. The API cache (D-P11) then serves a COMPLETED run whose output differs from what the current code produces, and `churn-survival audit` FAILs with no config change to explain it. None of this is recorded in `AGENTS.md`. | Bump the affected versions (`node5` v2, Node 3 config or `signal_version`, `validation_version`, adapter versions), or hash the loaded config `model_dump` plus a code-semantics version into `config_versions`. Record the changes as deliberate contract changes, and re-verify the dataset-7 hashes and the Node 3 golden κ. |
| N-H4 | ML / CR / TA ✔ verified | `node5/node.py:202-222` (`explain_all`) | The circuit breaker is checked only between batches (`while … not self.circuit_open`). Every result in the current batch is applied after the circuit opens. | With concurrency 4 and threshold 3, accounts 1–3 fail and account 4 still keeps its LLM text, whereas a sequential run would use the template. This contradicts the claim that "output is byte-identical to sequential". `NODE5_LLM_MAX_CONCURRENCY` is excluded from `run_id`, yet it changes the output. | Once `circuit_open` is set, discard the remaining results of that batch in input order. Add a test comparing concurrency 4 against 1 where 3 of 4 accounts fail. |
| N-H5 | SF ✔ verified | `node3/llm_extractor.py:597-627`, `node3/node.py:156-186`, `orchestration/graph.py:493-501` | A total Node 3 LLM outage (401, timeouts) makes every thread `LLM_EXTRACTION_FAILED`. The only signal is per-thread `errors`. The `warnings` list has no `n_threads_failed` entry ✔, and there is no circuit breaker. | An expired key across 5,000 threads gives a COMPLETED run with empty warnings. The report is silently quantitative-only, with one failed call per thread. | Add a run-level warning ("N of M threads failed LLM extraction") and propagate it into `state.warnings`. Add a consecutive-failure breaker like Node 5's. |
| N-H6 | API / SEC / CR | `api/app.py` (`_limit_body_size`), `api/routes/uploads.py`, `api/routes/runs.py` | FastAPI reads the body (form or JSON) before it resolves dependencies, so `require_auth` and `require_writes` run after the whole body is read. The size middleware checks only `Content-Length`, which chunked bodies bypass. The executor queue has no bound and `POST /runs` has no rate limit. | An unauthenticated client on the default writes-off server streams a chunked multipart body to `/uploads`, and Starlette spools it to disk before returning 401 or 403. A key holder can queue unlimited content-addressed runs by varying the inputs. | Add a pure ASGI middleware that counts received bytes and rejects unauthenticated or writes-disabled requests before reading the body. Cap queued plus running runs (429) and rate-limit `POST /runs` and `/uploads`. |
| N-H7 | SEC / API / PY / CR | `router/llm_mapper.py:429-470` (`confirm_and_persist`), `api/routes/mappings.py:142-150` | Duplicate check → `open("x")` on a second-resolution `map_<ts>.json` is a check-then-act race, and the write is not atomic. | Two confirms for the same shape in different seconds (a double-click, or two operators) write two configs with the same `headers_hash`. Every later run then fails loading adapters until someone deletes a file by hand. A concurrent run can also read a half-written file. | Hold a process lock across check and write, and re-check after writing. Write to a temp file and then `os.replace`. |
| N-H8 | API / PY / CR | `node2/artifact.py:91-108`; `config/settings.py` (`RUN_MAX_WORKERS` default 2) | `model.json`, `model.joblib` and `model.sha256` are written in place, one after another, into a content-addressed directory. | Two concurrent `persist_artifact=True` runs with the same `model_version` (different `run_id`s) interleave writes. The digest then never matches and `load_artifact` refuses the model, or a reader sees a truncated `model.json`. | Write to a temp directory and rename it, write the digest last, or skip the write when a matching digest already exists. |
| N-H9 | CR | `frontend/static/views/report.js` ("Open static report"), `api/app.py:165` | After the H1 fix, `GET /runs/{id}/report.html` requires `X-API-Key`. A plain `<a target=_blank>` navigation cannot send the header. | With `API_KEY` set, the link returns 401 JSON. This affects the current local setup. | Fetch it with the key (`api.getReportHtml`) and open a blob URL. Alternatively, use a short-lived signed URL. |
| N-H10 | SEC / TS / TA | `frontend/static/components/ui.js` (`downloadCsv`), `views/report.js` | CSV export quotes only `"`, `,` and `\n`. Cells that start with `=`, `+`, `-`, `@`, tab or CR are written raw. A bare `\r` is not quoted, and the object URL is revoked synchronously. | A customer name from an uploaded dataset, such as `=HYPERLINK(...)`, executes when an analyst opens the export in Excel or Sheets. | Prefix formula-leading *string* cells with `'` and quote `\r`. Revoke the URL on a later tick, and add a BOM for Excel. |

### MEDIUM

| # | Area | Location | Issue | Fix |
|---|---|---|---|---|
| N-M1 | SF | `node5/node.py:158,207,227`; `api/service.py:43-54,251` | An LLM that is configured but disabled by config (or the reverse) gives no signal. `llm_calls=0` and `llm_failures=0` look identical to a healthy deterministic run. | Warn when exactly one of `client` and `llm_enabled` is set, and record the reason in the processing report. |
| N-M2 | SF / API | `api/service.py:209-217, 258-266` | A failed run stores only `type(exc).__name__`, logged without `exc_info`. A persist failure is reported as a pipeline failure and the completed result is discarded. | Log with `exc_info`, store a sanitised message, and distinguish `PERSIST_ERROR` from pipeline failure. |
| N-M3 | API | `api/routes/runs.py:171-183` | If `executor.submit` (or `update_fields`) raises after `claim`, the row stays PENDING. Every retry is then treated as in-flight and `force` returns 409. | Wrap `submit`, and on failure mark the row FAILED and return 503. |
| N-M4 | SF | `node5/llm/explainer.py:103-125` | A 401 or timeout is counted as a validation rejection ("model wrote unsupported claims"). Only the last rejection survives. | Keep separate counters for provider errors and rejections, and trip the breaker immediately on 401 or 403. |
| N-M5 | PY | `node5/llm/explainer.py`, surfaced by `_LlmBudget.warnings` | `str(exc)` from a Pydantic `ValidationError` or a validator violation can embed raw LLM output. That output reaches the persisted `node5.json` warnings and the API. | Reduce to a reason code, as `node3/llm_extractor._failure_detail` does. |
| N-M6 | SF | `orchestration/persistence.py:294-300,355-366`; `orchestration/index.py:336-344`; `orchestration/gc.py:129-167` | Four problems: (1) self-heal runs only when the index is empty; (2) corrupt run directories are skipped silently; (3) undecodable rows are dropped while `COUNT(*)` still counts them; (4) the GC count-prune treats an unreadable `summary.json` as deletable, so a RUNNING run on a flaky OneDrive read could be pruned. | Log and count skipped directories, reconcile when there are more directories than rows, and skip plus record runs with unreadable metadata in GC. |
| N-M7 | TS | `frontend/static/app.js:118-121`, `views/mapping.js`, `components/filePicker.js` | A render still awaiting its data can write into the next view's shared `#view`: it clears it on error, the mapping steps get appended under Upload, and banners appear on the wrong screen. | Use a route generation token or a per-render host, and ignore stale completions. |
| N-M8 | TS | `frontend/static/api.js:15-22` | `localStorage` reads are unguarded at module top level. | With storage blocked, a SecurityError aborts `app.js` and the page is blank. Wrap the reads in try/catch with an in-memory fallback. |
| N-M9 | TS | `views/runStatus.js:144-161, 114` | Two problems: (1) one failed poll (a network blip or a 502) stops polling permanently; (2) "Resubmit run" sends only `raw_path`, which drops the support threads and `node1_version` while the copy says "same input". | Retry with backoff on status 0 or 5xx. For Resubmit, send the inputs or reword the copy. |
| N-M10 | TS | `views/mapping.js:72-85,203-217` | `updateChecks` re-enables Confirm while a request is in flight, which allows a double-submit. Edits made after a successful confirm, when the trigger then fails, are never persisted. | Add an in-flight flag, and reset `confirmed` on edit or lock the editor. |
| N-M11 | TS | `components/ui.js` (`horizonBand`) | "N accounts assessed" is a client-side sum that includes insufficient-data accounts, which are by definition not assessed. | Use an API total, or exclude insufficient-data accounts and relabel. |
| N-M12 | PY / TA ✔ verified | `router/llm_mapper.py:75-84` (`_retry_delay`) | `Retry-After: nan` gives `nan` after the clamp, and then `time.sleep(nan)` raises `ValueError` ✔. | Guard with `math.isfinite`. |
| N-M13 | PY / TA | `orchestration/routing.py:52-77` | The fingerprint cache is keyed on (path, size, mtime_ns). A same-size rewrite with a coarse or restored mtime (OneDrive, `copystat`) returns a stale fingerprint, which gives wrong routing and a wrong `run_id`. | Add a cheap header-line hash to the key, or re-stat after parsing. |
| N-M14 | ML | `node2/cox.py:167-200`, `node2/node.py:340-342` | `survival_ci` ignores `exp(x·β)` and the strata, yet ships as a 95% CI. It can also be NaN, which is written into `HorizonResult.ci` and is not valid JSON. | Use the baseline covariance or a bootstrap, or mark it approximate. Map NaN to `None`. |
| N-M15 | API | `api/routes/uploads.py:205-217` | `target.exists()` then `os.replace` lets concurrent same-name uploads overwrite each other, which breaks the "never replaced by different content" guarantee. | Publish with `os.link` and catch `FileExistsError`, then compare hashes, or use a lock. |
| N-M16 | API | `api/routes/runs.py:78-87` | `GET /runs` has no offset or cursor, so runs beyond 1000 are unreachable. | Add `offset` or `created_before`. |

### Accessibility (WCAG 2.2 AA)

| # | SC | Location | Issue | Fix |
|---|---|---|---|---|
| N-A1 | 1.4.3 ✔ verified | `app.css` `--high` #c7643d, `--watch` #a8792b | These colours are used as text on paper in the light theme at **3.28:1** and **3.21:1** ✔. That covers the High and Medium badges, legend labels and `.status-stopped`. (Muted text is 4.59, critical 4.63, stable 4.95.) | Add darker text variants (≥4.5:1), and keep the current colours for rails and the band (3:1). |
| N-A2 | 2.4.3 / 4.1.2 | `views/report.js` drawer | `aria-modal` is set, but there is no focus trap and `.layout` is not inert. | Set `inert` on `.layout` while the drawer is open, and add a Tab wrap. |
| N-A3 | 1.3.1 / 4.1.2 | `components/filePicker.js:27,90`, `views/upload.js:55` | The file-picker `<select>` has no label, the mapping-file input has no label, and every file input is named "or upload:". | Pass a `label` option and render a `<label for>`. |
| N-A4 | 2.4.3 | `views/report.js` (chips), `views/history.js` (filters) | Re-rendering the chips destroys the focused button, and "Show more" hides itself while it has focus. | Update `aria-pressed` in place, and move focus sensibly. |
| N-A5 | 1.4.11 | `app.css` form fields | Input and select borders are about 1.9:1 (light) and 2.4:1 (dark), below the required 3:1. | Use a solid border colour of about #7a8791 (light) or #6b7d86 (dark). |
| N-A6 | 2.4.2 / 4.1.3 | `app.js` routing, `views/runStatus.js`, banner | `document.title` never changes and route changes are not announced. The status live region re-announces every 2 s poll. FAILED and STOPPED sections are not announced. The banner role is set after its content. | Set the title per route and focus the h1. Update live text only on change. Use persistent `status` and `alert` regions. |
| N-A7 | 1.4.1 / 1.3.1 | `components/stageTracker.js`, `app.css` | "Done" is shown by colour only, and step names are `display:none` on mobile, which hides them from assistive technology too. | Add visually hidden "(completed)" and "(current)" text, and hide labels with an sr-only class. |
| N-A8 | 4.1.2 | `components/ui.js` (`actionRow`) | `<tr tabindex=0 aria-label=…>` replaces the row's content name. | Put a real button or link in the name cell as the tab stop. |

Minor accessibility items: no `aria-current="page"` on the navigation and no skip link; legend buttons expose only their `title`; font sizes are fixed in px; scroll regions are not labelled; truncated values are available only through a `title` tooltip; `scrollIntoView` smooth scrolling ignores reduced motion.

### LOW / INFO

- **Symlinks (SEC):** `resolve_raw_path` returns the unresolved path, and `/raw-files/{name}` follows symlinks. This needs local filesystem access to exploit.
- **Logging and the API key (SEC):** `api/service.py:184` logs a raw `str(exc)`, which should be redacted. The API key is stored in `localStorage`, so `sessionStorage` would be better. Remove the unused `html` branch from `el()`.
- **Stored data and API docs (API):** stored JSON that fails validation returns a bare 500. OpenAPI registers no `APIKeyHeader` scheme, and `POST /runs` responses are undocumented. `GET /raw-files` exposes absolute server paths and runs `mkdir` on a GET. `supersedes_run_id` is unvalidated. Startup recovery is unsafe with more than one process.
- **LLM client (PY):**
  - the shared `httpx.Client` is never closed and is not fork-safe
  - read timeouts are retried in full, about 3 × 60 s per call
  - `_raised_by_this_codebase` whitelists messages by the path of the raising file, which is fragile
  - `resolve_node1_version` silently falls back to a 0.0 threshold
- **Dead code (PY / SF):** the duplicate `except` arms in `node3/llm_extractor.py:~598-606` are dead code.
- **Artifacts and audit (CR / ML):**
  - artifacts saved before `model.sha256` existed now fail to load
  - the digest sits next to the pickle, so it guards against corruption, not tampering
  - the reproducibility audit does not skip LLM-enabled runs
  - upsert `COALESCE` can never clear operational fields
  - CI `pip-audit` has no allow-list
  - Node 3 routes collapsed or out-of-universe threads offline
- **Frontend (TS):**
  - Export can use a stale (debounced) query
  - a legend filter can dead-end when fewer than 2 chips are present
  - `api.js` logs every request to the console
- **Repo hygiene (CR):** the new test files and `REVIEW.md` are untracked and must be added; `config/mappings/map_20260927T181905Z.json` is staged as a delete.

### Test gaps (TA)

| # | Crit. | Gap | Test to add |
|---|---|---|---|
| T1 | 7 ✔ verified | The suite depends on the developer's `.env`: about 580 tests fail when `LLM_PROVIDER` is set with no key. | An autouse fixture in `tests/conftest.py` that forces `LLM_PROVIDER=none`, deletes the key and model, and clears the `get_settings` cache. |
| T2 | 7 | Fingerprint cache: same-size rewrite with restored mtime, LRU eviction at 8, and thread safety. | `test_same_size_header_change_with_restored_mtime_is_detected` (expected to fail today), `test_cache_evicts_lru_beyond_8`, `test_fingerprint_file_thread_safe`. |
| T3 | 6 | Mapping confirm: a same-timestamp collision never overwrites; a corrupt `map_*.json` is skipped by `find_confirmed_mapping`; the 409 path writes no second file. | Tests in `tests/router/test_llm_mapper.py` and `tests/api/test_mapping_endpoints.py`. |
| T4 | 6 | Node 5 pool: an unexpected client exception falls back to the template; breaker order across a batch boundary; `llm_model_version` is `None` when disabled. | Tests in `tests/node5/test_llm_budget.py` (pairs with N-H4). |
| T5 | 6 | Node 3 concurrency with mixed failures and quarantines matches sequential, including the order of the errors list. | `test_mixed_failures_match_sequential`, `test_worker_exception_quarantines_only_that_thread`. |
| T6 | 5 | `LlmClient` retry edges: clamp of 3600 to 30, HTTP-date, negative values, 429 on the last attempt raises, timeout retried. | Tests in `tests/router/test_llm_client.py`. |
| T7 | 5 | `/mappings/draft` LLM rate limit (429 after 10 per minute; the deterministic path is not limited; the key is not leaked). | Tests in `tests/api/test_mapping_endpoints.py` with a reset fixture. |
| T8 | 5 | Mapping-adapter refactor equivalence: compiled op against a reference implementation for every op, and a pinned `records` SHA for dataset 7. | `test_compile_matches_apply_for_every_op`, `test_transform_output_stable_for_dataset7`. |
| T9 | 4 | Frontend logic (CSV escaping, filter preserves API order, paging bounds, checklist) is asserted only as string presence. | Extract the pure functions into `frontend/static/lib/` and run them with `node` from pytest. |

### Suggested order

1. **Batch A: frontend and quick safety, low risk.**
   - N-H9, N-H10, N-M7, N-M8, N-M11
   - N-A1…N-A3
   - N-M12, T1
2. **Batch B: backend robustness.**
   - N-H5, N-H6, N-H7, N-H8
   - N-M1…N-M3, N-M15
3. **Batch C: needs an owner decision, because these touch frozen nodes and run identity.**
   - N-H1…N-H4, N-M14
   - Then re-verify the dataset-7 hashes and the Node 3 golden κ, and record the changes in `AGENTS.md`.

---

## 7. Remediation status (2026-10-01)

Every §6 finding was addressed on `fix/review-batch-a-frontend-safety` (uncommitted). Verification: `pytest` **1352 passed, 2 skipped, 0 failed** (coverage 94%); `ruff check .` and `mypy schemas node3` clean; `scripts/validate_dataset7.py` 58/58; Node 3 golden κ gate green; Edge click-through of the UI 28/28.

| Finding | Status | Where |
|---|---|---|
| N-H1 | Fixed. `FIT_ALGORITHM_VERSION` is part of `model_version`; `modeling_version` → 1.1.0; stratified coefficients pinned in a test. | `node2/artifact.py`, `tests/node2/test_assumptions.py` |
| N-H2 | Fixed. Stratified fits rank the delivered `1 − S(t_ref)`; the metric is labelled `c_index_kind="apparent"` (UI: "C-index (apparent)"). No held-out estimate was added. | `node2/assumptions.py`, `node2/node.py` |
| N-H3 | Fixed. `CODE_SEMANTICS_VERSION` is in every run identity; Node 1/3/5 provenance versions and adapter versions bumped; audit skips `SEMANTICS_CHANGED`. | `orchestration/identity.py`, configs |
| N-H4 | Fixed. The rest of a batch is discarded once the breaker opens; concurrency 4 matches 1. | `node5/node.py` |
| N-H5 | Fixed. Run-level warning (also in `state.warnings`), plus an ordered consecutive-failure breaker. | `node3/node.py`, `orchestration/graph.py` |
| N-H6 | Fixed. Pure-ASGI body guard (auth, writes and size before the body is read; chunked bodies counted), queue cap, per-client rate limits. | `api/app.py`, `api/limits.py` |
| N-H7 | Fixed. Thread lock + lockfile around check-and-write, atomic hard-link publish, unique names. | `router/llm_mapper.py` |
| N-H8 | Fixed. Staged directory published with one rename; a complete artifact is kept; partial ones repaired. | `node2/artifact.py` |
| N-H9 | Fixed. The report is fetched with the key and shown in a sandboxed `srcdoc` iframe. | `frontend/static/api.js` |
| N-H10 | Fixed. Formula prefix, `\r` quoting, BOM, CRLF, deferred revoke; tested under Node. | `frontend/static/components/ui.js` |
| N-M1 – N-M16 | Fixed (N-M14: CI marked approximate and NaN → `None`; no exact CI method was added). | see `AGENTS.md` status entry |
| N-A1 – N-A8, minors | Fixed. | `frontend/` |
| LOW / INFO | Fixed, except: the artifact digest still guards corruption, not tampering (documented); "Node 3 routes collapsed or out-of-universe threads offline" is intended behaviour. | various |
| T1 – T9 | Added. | `tests/` |

**Found during remediation (not in this review):** customers without support threads were stamped `model=offline` while LLM-processed customers carried the client model. Node 5's MIXED_PROVENANCE gate therefore refused to publish **every** LLM run with partial support coverage. Node 3 now records the run's extraction model for every customer. A regression test is in `tests/orchestration/test_sequencing.py`.

**Behaviour changes to note:** every `run_id` changed once (semantics version); `GET /raw-files` and `POST /uploads` now return bare file names as `raw_path`; the API key is kept in `sessionStorage` (re-enter it per browser session); triggers, uploads and queued runs have default limits (30/min, 20/min, 16).

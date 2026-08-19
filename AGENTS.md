# AGENTS.md

Project context for AI agents working in this repository. Read this first.

## What this project is

Churn Survival Analysis System — a 5-node Python pipeline that turns heterogeneous customer data into a ranked, explainable customer-risk report:

- **Node 1** — canonical schema + adapters (deterministic preferred, LLM only for one-time mapping)
- **Node 2** — survival model (CoxPH + Kaplan-Meier fallback)
- **Node 3** — qualitative support signal extraction + evidence
- **Node 4** — deterministic synthesis → ranked account list
- **Node 5** — client-facing risk report

## Source of truth

- `architecture.md` — **the authority.** Every contract, schema, rule, formula, and non-negotiable. ~3000 lines. Do not rescan fully; read the specific section(s) the task references.
- `ROADMAP.md` — the implementation sequence. Every task cites its architecture sections. Start here to know what to build next.

If anything appears to conflict, `architecture.md` wins.

## Current status

- **Phase 1 (Shared Schema Foundation) — complete.** Tasks 1.1–1.4 done:
  `schemas/` Pydantic contracts (canonical, validation, mapping, node2–5) with
  centralized StrEnum vocabulary (`schemas/enums.py`), strict models
  (`extra="forbid"` except open containers), frozen versioned config models +
  JSON/YAML loader (`config/models.py`, `config/loader.py`; real config files
  deferred to Phase 5), and schema strictness tests (91 passing; ruff + `mypy schemas` clean).
- **Phase 2 (Node 1 — Router + Adapters + Canonicalization) — complete.** Tasks 2.1–2.12 done:
  `adapters/` (protocol + `BaseAdapter`, `util`, `tenure`, `_table`, clean_csv,
  excel_multi_sheet, stripe_customers, hubspot_crm, zendesk_intercom,
  mapping_adapter), `router/` (fingerprint, router, llm_mapper), `node1/`
  (validation hard gates, report, feature gate, entry point), versioned
  `Node1Config` (`config/node1/v1.json`) + loader, and `config/mappings/` for
  human-confirmed mapping configs. `pipeline/main.py` dispatches `node1` and `map`.
  ruff + `mypy schemas` clean; `churn-survival node1 <raw>` exits 0.
- **Phase 3 (Node 2 — Survival Model) — complete.** Tasks 3.1–3.12 done:
  `node2/` (eligibility gates, multicollinearity warnings, CoxPH path with
  mandatory penalizer + delta-method survival CIs + seeded bootstrap C-index,
  PH-violation severity → one managed stratified refit, data-driven horizon
  availability, Kaplan-Meier fallback, cold-start states, deterministic
  interpretation, versioned artifact + JSON sidecar, status state machine,
  fit/score-separated entry point + CLI). `config/node2/v1.json` + loader;
  `Node2Output.customer_ids` parallel to `customer_states` (alignment contract).
  `pipeline/main.py` dispatches `node2`. CLI:
  `churn-survival node2 <raw-file> [--config <node1_version>] [--model-config <node2_version>]`
  runs Node 1 in-process, persists `models/<model_version>/model.json` + `model.joblib`.
  415 tests passing; coverage ≥ 90% on `node2/`; ruff + `mypy schemas` clean;
  real-data E2E `churn-survival node2 data/raw/telco-customer-churn.csv --config telco`
  → `cox_ph` WARNING (7032 scored, 1869 events, horizons 30/90/180 AVAILABLE);
  re-runs bit-identical (same `model_version`).
- **Real-data ingestion demo — done (IBM Telco Churn, 7,043 rows).** Full path
  exercised on real data: router correctly refuses unknown shapes
  (`UnmappedFormatError`), the LLM mapping-report workflow + human confirmation
  produced a deterministic `MappingConfig` (`config/mappings/map_*.json`), and the
  snapshot dataset ingests via `churn-survival node1 data/raw/telco-customer-churn.csv --config telco`
  → `PASSED accepted=7043`. This added two audited transform ops to the mapping
  adapter — `months_before(reference_date)` and `snapshot_end(reference_date)`
  (documented in architecture §1.6) — a deployment-scoped `config/node1/vtelco.json`
  with its own `approved_core_keys`, a `--config <version>` CLI flag on node1, and
  the deployment-union `CoreFeatures` schema (architecture §1.3: all known core
  vocabularies optional at schema level, per-deployment gating via config).
- **Multi-company onboarding — done (2nd real dataset: UCI Iranian Churn, 3,150 rows).**
  Onboarding is now config-driven + guided: core types are declared per deployment
  (`Node1Config.core_key_types`, replacing hardcoded `NUMERIC_CORE/STRING_CORE`),
  `mapping_adapter._coerce_for_key` passes values through (types are the config's job),
  the mapping adapter's third audited op `row_number` synthesizes deterministic
  `customer_id`s when a source has no ID column, and the CLI has a `map` subcommand
  (`churn-survival map <file>` drafts a skeleton MappingReport → user fills it →
  `--confirm` persists `config/mappings/map_<ts>.json`) plus a guided
  `UnmappedFormatError` checklist (see `docs/onboarding.md`, `config/node1/_template.json`).
  Confirmed adapters load only from `map_*.json` (drafts live in `config/mappings/drafts/`),
  duplicate fingerprints fail loudly, and the router evaluates every registered adapter
  (confirmed mappings `confidence=1.0/priority=0` always win for their fingerprint,
  all matches surfaced as `matched_candidates` in the report + CLI).
  `churn-survival node1 data/raw/iranian-customer-churn.csv --config iranian`
  → `PASSED accepted=3150` (reuses `usage_frequency` from the core union).
  269 tests passing; coverage 98% on `node1/`, `adapters/`, `router/`;
  ruff + `mypy schemas` clean; both real-data CLI E2Es exit 0.
- **3-dataset onboarding test — done (Bank, Cellular, Credit Card; 91,174 rows).**
  The guided flow was exercised on 3 more independent churn datasets from 3 different
  sources: Bank Customer Churn (`dad50914…`, 10,000 rows, `Tenure`/`Exited`),
  Cell2Cell Telecom (`774dc5f8…`, 71,047 rows — a wide 78-column stress test; the
  74 unmapped columns are stored as extras under their raw names), and Credit Card
  Customers (`dd227148…`, 10,127 rows — `Attrition_Flag` mapped to `event_observed`
  via the categorical `map({...})` op; two `Naive_Bayes_Classifier_*` columns kept
  as extras only, rule 5). All three ingest with empty `approved_core_keys`
  (`config/node1/vbank.json`, `vcellular.json`, `vcredit.json`).
  `churn-survival node1 data/raw/{bank-customer-churn,cell2cell-churn,credit-card-customers}.csv --config {bank,cellular,credit}`
  → `PASSED accepted={10000,71047,10127}`, all exit 0. No new ops were required
  (every mapping reused `to_int`/`months_before`/`snapshot_end`/`map`), so the
  audited op list is unchanged.
  308 tests passing; coverage 98%; ruff + `mypy schemas` clean; five real-data
  CLI E2Es exit 0.
- **Event-normalization bugfix (adapter transform).** `MappingConfigAdapter.transform`
  no longer re-runs `to_int` on the *result* of `status_to_event` (which was always
  `None` for numeric-string events like `"1"/"0"`, silently rejecting valid rows via
  Gate 6). The `to_int` fallback now receives the original raw value (mirrors
  `obvious_row_maps`). Guard added: `build_report` raises on any
  `n_accepted + n_rejected != n_input_rows`, and the mapping adapter raises if
  `transform` returns fewer records than input rows — rows can never silently
  vanish. Regression tests cover numeric-string events through `str.strip()`.
  None of the five onboarded datasets were affected (all map `event_observed` via
  `to_int` or `map({...})`, which yield `int` directly; accepted counts unchanged).
- **Gate 6 hardened (validation).** `node1/validation.py` now rejects
  `event_observed` that is not *exactly* the int `0`/`1` — previously
  `event not in (0, 1)` let `1.0/0.0/True/False` slip through via Python equality
  coercion and be silently coerced by `Literal[0,1]`. The gate now rejects bool
  explicitly (`isinstance(event, bool)` — bool is an int subtype) plus any
  non-int (float/str/None/numpy scalars). Regression tests cover `1.0`, `0.0`,
  `True`, `False`, `"1"`, `2` (rejected) and `1`, `0` (accepted). Full suite
  confirms no shipped adapter relied on float/bool events.
- **Gate 3 hardened (date validation).** `node1/validation.py` `_parse_iso` now
  accepts only the exact extended-format ISO 8601 calendar date (`YYYY-MM-DD`).
  Python 3.12's `date.fromisoformat` also parses basic (`"20260801"`) and
  week-date (`"2026-W33-1"`) formats, which `CanonicalRecord` then rejects — so
  such values previously passed Gate 3 and crashed `build_report` with a
  `RuntimeError` instead of being quarantined. A strict regex now rejects them
  with `INVALID_DATE` at Gate 3 (also blocks Python 3.13's non-padded forms).
  Regression tests cover `"20260801"`, `"2026-W33-1"` (rejected, no crash) and
  `"2026-08-01"` (accepted).
- **Missingness counts empty string as missing (validation).** `_core_value_missing`
  now treats `""` in a core feature identically to `None` when computing the §1.7
  batch missingness fraction (scope unchanged: approved `core_features` keys only,
  never `extra_features`). Regression tests assert `plan_tier=""` == `None` and
  re-verify the 30% boundary with `None`/`""` behaving identically (2/3 → batch
  failed, 1/4 → passes). No accepted-count change on the five onboarded datasets
  (telco/iranian core columns have zero empty strings; bank/cellular/credit have
  empty `approved_core_keys`).
- **Feature-gate/Gate-8 contradiction resolved (demote + warn, not reject).**
  The pipeline now runs `feature_gate_records` *before* validation, so unapproved
  core keys are demoted to `extra_features` (their original intent in §1.8)
  instead of being quarantined by Gate 8's `CORE_KEYS` check — the demotion path
  was previously unreachable dead code. Demotion is surfaced, not silent:
  `ValidationReport.demoted_features` records `{key: record_count}` (single
  source of truth) and human-readable warnings are formatted from it into
  `warnings`; the CLI prints demotions when present. Gate 8's `CORE_KEYS`/
  `CORE_TYPE` checks remain strict as defense-in-depth for direct
  `validate_records` callers. Regression tests: unapproved core key → record
  ACCEPTED + field in `extra_features` + `demoted_features` counts; wrong-typed
  APPROVED key → still rejected `CORE_TYPE` with zero demotions. The five
  onboarded datasets are unaffected (accepted counts unchanged; zero demotions
  since every mapping aligns with its deployment's `approved_core_keys`).
- Next work is **ROADMAP Phase 4 — Node 3** (Support signal extraction + evidence).
- Keep this status section accurate; update it as phases complete.

## Planned repo layout (ROADMAP Task 0.2 / architecture §8.10)

```
churn_survival/
├── adapters/               # deterministic adapters + base class
├── schemas/                # Pydantic models (canonical, reports, outputs)
├── router/                 # signature detection + routing logic
├── node1/ ... node5/       # one package per node
├── models/                 # saved model artifacts + mapping configs
├── orchestration/          # LangGraph graphs
├── api/                    # FastAPI routes (later)
├── tests/
├── config/                 # versioned config files (thresholds, vocab, prompts)
├── data/raw|processed/
└── pyproject.toml
```

## Hard rules (never violate)

1. **Determinism** — same inputs + same config versions → bit-identical output. `reference_date` is a declared cut-off, never "today" at runtime.
2. **LLM has zero authority** — over any score, rank, risk level, confidence, or evidence. Optional, explanation-polish only (Node 5) or thread-level extraction (Node 3). Deterministic fallback is mandatory.
3. **The combined score alone can never produce Critical.** Explicit critical rules only (Node 4 §4.10).
4. **Statistical work stays in plain Python functions** (lifelines, pandas, numpy). LangGraph is for orchestration only.
5. **Pydantic contracts are strict.** `extra_features` and `key_themes` are the only open dicts; never auto-feed them to a model.
6. **The system must be able to say "I don't know"** — `INSUFFICIENT_DATA`, `no_data`, `not_enough_data`, `explanation: null` are first-class states, not failures.
7. **No future leakage** — `observation_end <= reference_date` for every record.
8. **No secrets in git** — `.env` is never committed; use `.env.example` + pydantic-settings.
9. **Node 1 is frozen.** Do not modify Node 1 unless implementation of a later node exposes an actual contract defect or integration bug.

## Commands

The repo root *is* the package root (flat layout per architecture §8.10), so module
paths are top-level (e.g. `schemas.canonical`, `config.settings`, `pipeline.main`).
Run everything from the repo root with the venv active (`uv run ...`):

```bash
pip install -e ".[dev]"
pytest                       # run tests
pytest --cov                 # coverage
ruff check .                 # lint
mypy schemas                 # typecheck (strict for schemas, see pyproject overrides)
churn-survival <node>        # pipeline entry (skeleton; exits non-zero while nodes are stubs)
```

## How to work here

1. Check `ROADMAP.md` for the current phase/task and its stated `Verification`.
2. Read only the architecture sections the task cites.
3. Implement, then prove the task's verification bar (usually fixture-based tests).
4. Update this file's status + ROADMAP checkboxes when a phase completes.

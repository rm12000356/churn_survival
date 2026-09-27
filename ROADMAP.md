# Churn Survival System — Implementation Roadmap

**Version:** 1.0
**Date:** 2026-08-17
**Companion to:** `architecture.md` (v1.2 + Node 3/4/5 specs)

## Source of Truth

`architecture.md` is the single source of truth. Every architecture section number referenced below (e.g. `§1.3`) points into that document. This roadmap is the *sequence* of work; the architecture is the *contract*. When they appear to conflict, the architecture wins.

## Notation Legend

Every task below is structured as:

- **Objective** — what this step accomplishes.
- **Architecture refs** — the section(s) of `architecture.md` this task implements.
- **Deliverables** — files/artifacts produced.
- **Considerations** — what the architecture demands you keep in mind while implementing.
- **Verification** — how to prove the step is done.

## Global Acceptance Bar (applies to every phase)

1. Determinism: identical inputs + identical config versions → bit-identical output (`§4.27`).
2. The system is allowed to say "I don't know" (`§6`).
3. Every model/run records its versions (`§2.11`, `§3.13`, `§4.28`, `§5.25`).
4. LLM has zero authority over any score, rank, risk level, or confidence (`§4.1`, `§5.2`).
5. Statistical computation lives in plain Python functions; orchestration may use LangGraph (`§6`).
6. Nothing silently fails: every failure is a structured error or warning (`§1.7`, `§3.12`, `§4.26`, `§5.4`).

---

## Phase 0 — Scaffolding & Environment

### Task 0.1 — Python runtime pin + agent context
- **Objective:** Lock the interpreter so behavior is reproducible; give future agents a durable orientation file.
- **Architecture refs:** §8 (Python 3.11/3.12).
- **Deliverables:** `pyproject.toml` with `requires-python = ">=3.11,<3.13"`; `.python-version`; `AGENTS.md` at project root (project summary, source-of-truth pointers to `architecture.md` + `ROADMAP.md`, current phase status, hard rules from §6/§4.27/§5.34, repo layout, and the commands below). Keep it short — it points to the source-of-truth docs instead of duplicating them.
- **Considerations:** Pick 3.11 or 3.12 and pin it in CI; do not chase newer runtimes mid-project. `AGENTS.md` must be updated as each phase completes so the current status stays accurate.
- **Verification:** `python --version` matches; `pip install -e .` succeeds; `AGENTS.md` reflects the current phase.

### Task 0.2 — Folder tree
- **Objective:** Create the structure the architecture specifies.
- **Architecture refs:** §8.10 (project structure).
- **Deliverables:**
```
churn_survival/
├── adapters/               # deterministic adapters + base class
├── schemas/                # Pydantic models (canonical, reports, outputs)
├── router/                 # signature detection + routing logic
├── node1/                  # validation, feature gate prep
├── node2/                  # eligibility, fit, score, fallback
├── node3/                  # support signal extraction
├── node4/                  # synthesis (ranked account list)
├── node5/                  # client-facing report
├── models/                 # saved model artifacts + mapping configs
├── orchestration/          # plain-Python orchestration (routing + sequencing)
├── api/                    # FastAPI routes (later)
├── tests/
├── config/                 # versioned config files (thresholds, vocab, prompts)
├── data/
│   ├── raw/                # never touched, read-only
│   └── processed/
└── pyproject.toml
```
- **Considerations:** `data/raw` is immutable; treat it as an audit input. `config/` holds every versioned JSON/YAML (mapping configs, `ACTION_RULES`, vocabularies, thresholds).
- **Verification:** Tree exists; empty packages importable.

### Task 0.3 — Dependency manifest
- **Objective:** Declare the exact stack from the architecture.
- **Architecture refs:** §8.11 (dependency highlights).
- **Deliverables:** `pyproject.toml` dependency groups:
  - **core:** `pandas`, `pyarrow`, `pydantic>=2`, `lifelines`, `scikit-learn`, `numpy`, `scipy`, `joblib`, `structlog`, `pydantic-settings`
  - **orchestration:** `langgraph`, `langchain-core`
  - **llm:** `httpx` + provider SDKs (provider-agnostic: provider selected at runtime, see Task 0.4)
  - **api:** `fastapi`, `uvicorn`
  - **dev:** `pytest`, `pytest-cov`, `ruff`, `mypy`
- **Considerations:** Keep the statistical core (Node 2) free of any LLM/orchestration dependency. Keep `pandera` optional.
- **Verification:** `pip install -e ".[dev]"` resolves cleanly.

### Task 0.4 — Configuration & secrets (`.env`)
- **Objective:** All configuration via environment, per the architecture.
- **Architecture refs:** §8.6 (pydantic-settings), §5.3/§4.2 (versioned configs).
- **Deliverables:** `.env.example` (see Appendix B), `.gitignore` (ignores `.env`, `data/`, `models/`, `__pycache__/`), `churn_survival/config/settings.py`.
- **Considerations:**
  - Provider-agnostic LLM: `.env` holds `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`; a thin client factory maps provider → SDK.
  - **No secrets in git, ever.** `.env` is never committed; `.env.example` contains placeholders only.
  - `REFERENCE_DATE` is a declared dataset cut-off, never "today at runtime" — reproducibility depends on this (`§1.3`).
- **Verification:** `pydantic-settings` loads a valid `.env`; missing required keys fail loudly.

### Task 0.5 — Logging foundation
- **Objective:** Structured logging from day one.
- **Architecture refs:** §8.8 (structlog).
- **Deliverables:** `churn_survival/logging_setup.py`; `LOG_LEVEL` from settings.
- **Considerations:** Every node logs its versions (`mapping_version`, `model_version`, etc.); logs are machine-parseable.
- **Verification:** A sample structured log line includes `event`, `level`, `node`, version fields.

### Task 0.6 — Test scaffolding
- **Objective:** Fixture-based testing infrastructure.
- **Architecture refs:** §8.9 (pytest, pytest-cov).
- **Deliverables:** `tests/conftest.py`, shared fixture factories (valid canonical records, valid threads, valid Node 4 output), `pytest.ini`/`pyproject` config with coverage.
- **Considerations:** Fixtures are the contract examples from the architecture (active/churned customer `§1.3`, mapping report `§1.6`, threads `§3.2`).
- **Verification:** `pytest` runs an empty-but-wired suite; coverage configured.

### Task 0.7 — Linting & typing
- **Objective:** Enforce code quality gates.
- **Architecture refs:** §8.9 (testing discipline).
- **Deliverables:** `ruff` config, `mypy` config (`strict` for `schemas/`).
- **Considerations:** Schemas get the strictest typing; statistical code typed pragmatically.
- **Verification:** `ruff check .` and `mypy churn_survival/schemas` pass.

### Task 0.8 — Green skeleton
- **Objective:** The empty pipeline fails loudly, never silently.
- **Architecture refs:** §6 (principles).
- **Deliverables:** a `pipeline/main.py` entry that raises `NotImplementedError` on unimplemented nodes.
- **Considerations:** "Must be allowed to say I don't know" starts here — no fake success.
- **Verification:** Running the skeleton exits non-zero with a clear message.

---

## Phase 1 — Shared Schema Foundation

### Task 1.1 — Pydantic canonical contracts
- **Objective:** Encode every external contract exactly.
- **Architecture refs:** §1.2, §1.3, §1.6, §2.11, §2.12, §3.2, §3.4, §3.5, §4.21, §4.22, §4.24, §5.9, §5.10, §5.11, §5.26.
- **Deliverables:** `churn_survival/schemas/`:
  - `canonical.py` (CanonicalRecord, §1.3 — `core_features` whitelisted via `extra="forbid"` on the core dict)
  - `validation.py` (Node 1 output contract, §1.2)
  - `mapping.py` (MappingReport, §1.6)
  - `node2.py` (Final output, §2.12; ModelArtifact, §2.11)
  - `node3.py` (SupportThread, ThreadSignals, RiskFlag, CustomerSupportSignals, AggregatedRiskFlag, output contract, §3.2/3.4/3.5/3.11)
  - `node4.py` (RankedAccount, StructuredReason, full output, §4.21/4.22/4.24)
  - `node5.py` (CustomerReport, ReportReason, ReportEvidence, report output, metadata, §5.9/5.10/5.11/5.26)
- **Considerations:**
  - `extra_features` and `key_themes` are open dicts; everything else is strict.
  - Optionality matches the contracts exactly (`risk_score: float | None`, `explanation: str | None`, etc.).
  - Node 2 output allows `survival_probabilities` statuses `AVAILABLE`/`INSUFFICIENT_DATA` (§2.7).
- **Verification:** Schema round-trip tests; invalid records fail validation.

### Task 1.2 — Centralized enums & constants
- **Objective:** One versioned home for every enumerated vocabulary.
- **Architecture refs:** §2.3 (model status), §3.4 (flag vocabulary), §3.7 (strength), §4.16 (reason types), §4.11 (critical reason types), §5.13 (evidence modes).
- **Deliverables:** `churn_survival/schemas/enums.py` (StrEnum for `ModelStatus`, `FlagType`, `SignalStrength`, `Severity`, `RiskLevel`, `ReasonType`, `EvidenceMode`…).
- **Considerations:** Vocabulary is versioned (`vocabulary_version`, `§3.13`). Adding a `flag_type` requires a version bump, not a silent edit.
- **Verification:** Enums serialize to the exact strings the contracts use.

### Task 1.3 — Versioned config loader
- **Objective:** All decision logic driven by versioned configuration.
- **Architecture refs:** §4.2 (Node 4 config), §5.3 (Node 5 config), §1.5 (mapping configs), §5.18 (`ACTION_RULES`).
- **Deliverables:** `churn_survival/config/loader.py` — loads JSON/YAML from `config/`, validates against Pydantic models, returns config objects carrying their `*_version` fields.
- **Considerations:** Configs are immutable at runtime; changes require a new file version. `reference_date` threads through every config.
- **Verification:** Config load validates; unknown versions warn/fail per policy.

### Task 1.4 — Schema strictness tests
- **Objective:** Prove the contracts reject what they must.
- **Architecture refs:** all contract sections.
- **Deliverables:** `tests/test_schemas/`.
- **Considerations:** Test both directions: valid fixtures pass, adversarial inputs fail (extra core key, negative tenure, `event_observed=2`, non-ISO dates).
- **Verification:** Full schema suite green.

---

## Phase 2 — Node 1 (Router + Adapters + Canonicalization)

- **Status:** complete — Tasks 2.1–2.12 verified (288 tests passing; coverage 98% on `node1/`, `adapters/`, `router/`; ruff + `mypy schemas` clean; five real-data CLI E2Es exit 0).
  **Node 1 is frozen.** Do not modify Node 1 unless implementation of a later node exposes an actual contract defect or integration bug.

### Task 2.1 — Adapter Protocol + signature declaration
- **Objective:** Define the adapter contract.
- **Architecture refs:** §1.4 (Adapter Protocol), §0.1 (signatures).
- **Deliverables:** `churn_survival/adapters/base.py` — `name`, `version`, `can_handle(sample)`, `matches_signature(fingerprint)`, `transform(raw_data, reference_date)`, `get_mapping_config()`.
- **Considerations:** `transform` must never emit non-canonical top-level fields or temp fields like `tenure_start_date` (§1.4). Adapters receive `reference_date` and own tenure calculation.
- **Verification:** Protocol tests using a stub adapter.

### Task 2.2 — Fingerprint extraction
- **Objective:** Capture the data shape the router needs.
- **Architecture refs:** §0.1, §1.6 (fingerprint fields).
- **Deliverables:** `churn_survival/router/fingerprint.py` — headers, sheet names, column set, sample dtypes, structural cues; `headers_hash` = sha256 of sorted headers.
- **Considerations:** Fingerprint must include schema signature, not sample statistics alone (§1.6 fingerprint rule).
- **Verification:** Known CSV and Excel files produce stable fingerprints.

### Task 2.3 — Router with confidence & priority
- **Objective:** Deterministic selection; LLM only on genuine unknowns.
- **Architecture refs:** §0.1 (Router Decision Rule).
- **Deliverables:** `churn_survival/router/router.py` — collect matching adapters, pick best (highest confidence or explicit priority); **never call LLM if a high-confidence deterministic match exists**.
- **Considerations:** The decision is explicit: no match → LLM mapping-report path.
- **Verification:** Tests: deterministic match wins; no match routes to LLM path; priority order respected.

### Task 2.4 — Deterministic adapters
- **Objective:** Pure Python parsers for known shapes.
- **Architecture refs:** §1.5.
- **Deliverables:** build in dependency order:
  1. `clean_csv.py` (single-sheet obvious columns)
  2. `excel_multi_sheet.py`
  3. `stripe_customers.py` (customer + subscription export)
  4. `hubspot_crm.py`
  5. `zendesk_intercom.py`
- **Considerations:** Start with CSV to prove the pattern; each adapter has its own `name`/`version`/signature and `get_mapping_config()` for audit.
- **Verification:** Each adapter transforms its fixture into valid canonical records.

### Task 2.5 — LLM mapping-report path
- **Objective:** One-time human-confirmed translation for unknown formats.
- **Architecture refs:** §1.6.
- **Deliverables:** `churn_survival/router/llm_mapper.py` — sample → LLM → Pydantic MappingReport → human review → confirmed config persisted in `config/mappings/` → future files matching fingerprint go deterministic.
- **Considerations:** LLM produces a report, **never** a direct transformation. Human confirmation is the final safety gate. `llm_model_used` and `generated_at` recorded.
- **Verification:** Mapping report schema round-trip; confirm → stored config; re-run takes deterministic path.

### Task 2.6 — transform() tenure & censoring
- **Objective:** Correct observation window math.
- **Architecture refs:** §1.3 (field rules, reproducibility rule).
- **Deliverables:** tenure helper (`(observation_end - observation_start).days`), censoring helper (active customers → `observation_end = reference_date`).
- **Considerations:** Rerunning identical raw on a later day must give identical tenure — never use "today". `tenure ≥ 0` and finite.
- **Verification:** Active vs churned fixtures match §1.3 examples (521.0 / 472.0 days).

### Task 2.7 — Validation hard gates
- **Objective:** Enforce every mandatory check.
- **Architecture refs:** §1.7.
- **Deliverables:** `churn_survival/node1/validation.py` implementing all checks:
  1. Required top-level keys
  2. `customer_id` non-empty + unique
  3. Valid ISO dates
  4. `observation_start ≤ observation_end`
  5. `tenure ≥ 0`, finite, equals date diff
  6. `event_observed ∈ {0,1}`
  7. **No future leakage:** `observation_end ≤ reference_date`
  8. `core_features` approved keys + types
  9. Missingness > 30% rejects
  10. No NaN/inf in numeric fields
  11. Statistical sanity of tenure distribution
- **Considerations:** Any failure stops or quarantines the affected record. Failures land in `validation_report.errors` as per-record dicts.
- **Verification:** One test per gate (positive + negative).

### Task 2.8 — Validation report contract
- **Objective:** Emit the exact Node 1 output.
- **Architecture refs:** §1.2.
- **Deliverables:** `churn_survival/node1/report.py` — `status` PASSED/FAILED/PARTIAL, counts, errors, warnings, `adapter_used`, `mapping_version`, `reference_date`. Complete validation failure → empty `canonical_dataset`, pipeline stops.
- **Considerations:** Never fabricate a PASSED.
- **Verification:** Contract schema test with PASSED/PARTIAL/FAILED scenarios.

### Task 2.9 — Feature Gate
- **Objective:** Separate storage from modeling eligibility.
- **Architecture refs:** §1.8.
- **Deliverables:** `churn_survival/node1/feature_gate.py` — new fields land in `extra_features` (stored permanently); promotion to `core_features` is an explicit, separate decision requiring non-missingness, variation, and association/domain approval; sparse events → reject promotion by default.
- **Considerations:** Multicollinearity signals are warnings, not automatic killers. `key_themes`/`extra_features` are never model inputs automatically.
- **Verification:** Promotion test: field stays in `extra_features` until explicit approval.

### Task 2.10 — Node 1 entry point
- **Objective:** Compose Node 1 as plain functions.
- **Architecture refs:** §1.1, §6 (statistical steps stay ordinary Python).
- **Deliverables:** `churn_survival/node1/node.py` — fingerprint → route → transform → validate → feature-gate → report.
- **Considerations:** No graph framework inside Node 1; LangGraph (Phase 7) calls this node.
- **Verification:** E2E fixture: raw CSV → valid Node 1 output.

### Task 2.11 — Reproducibility test
- **Objective:** Lock the reproducibility rule.
- **Architecture refs:** §1.3 (reproducibility rule).
- **Deliverables:** test that runs identical raw input twice with different "today" and asserts identical tenure/output.
- **Considerations:** Tenure for active customers is always vs declared `reference_date`.
- **Verification:** Two runs bit-identical.

### Task 2.12 — Node 1 test suite
- **Objective:** Comprehensive fixture coverage.
- **Architecture refs:** §8.9.
- **Deliverables:** `tests/node1/` — adapter tests, validation gates, mapping report tests, duplicate-id rejection, future-leakage rejection, missingness threshold, feature-gate tests.
- **Verification:** Coverage ≥ 90% on `node1/`, `adapters/`, `router/`.

---

## Phase 3 — Node 2 (Survival Model)

- **Status:** complete — Tasks 3.1–3.12 verified (415 tests passing; coverage ≥ 90% on `node2/`; ruff + `mypy schemas` clean; real-data telco E2E `churn-survival node2 data/raw/telco-customer-churn.csv --config telco` → `cox_ph` WARNING, 7032 scored, 1869 events, horizons [30, 90, 180] AVAILABLE, artifact persisted; re-run bit-identical).

### Task 3.1 — Model eligibility
- **Objective:** Hard gates before any fit.
- **Architecture refs:** §2.4.
- **Deliverables:** `churn_survival/node2/eligibility.py` — hard gates (survival columns, binary event, min customers, min events/predictors, variation, missingness, events-to-predictors) + warning signals (low events, elevated missingness, correlations, short follow-up, uneven distribution).
- **Considerations:** Eligibility is evaluated before fitting; a failure blocks CoxPH but allows KM fallback.
- **Verification:** Fixtures that trip each gate.

### Task 3.2 — Fit/score separation
- **Objective:** The mandated API shape.
- **Architecture refs:** §2.2.
- **Deliverables:** `fit_model(canonical_dataset, config) -> ModelArtifact` and `score_customers(model_artifact, customers) -> dict`.
- **Considerations:** Mandatory for scheduled retraining + exact historical rescoring.
- **Verification:** Fit once, score twice → identical scores.

### Task 3.3 — CoxPH path
- **Objective:** Fitted model with correct constraints.
- **Architecture refs:** §2.6.
- **Deliverables:** `churn_survival/node2/cox.py` — `lifelines.CoxPHFitter`, **mandatory `penalizer > 0` (L2)**, explicit tied-event handling, only approved `core_features` as predictors.
- **Considerations:** Penalizer is stored in the artifact (§2.11). Ties must be handled explicitly, not silently defaulted.
- **Verification:** Fit runs on synthetic data; penalizer recorded.

### Task 3.4 — Multicollinearity handling
- **Objective:** Warnings, not automatic kills.
- **Architecture refs:** §2.5.
- **Deliverables:** pairwise correlation check; VIF only when predictor count makes it meaningful.
- **Considerations:** With 2–3 features, interpretability and data quality take priority. Final keep/drop stays with Feature Gate.
- **Verification:** Correlated-features fixture produces warning, not removal.

### Task 3.5 — Assumption checks & validation
- **Objective:** Post-fit diagnostics.
- **Architecture refs:** §2.6 (Schoenfeld residuals, C-index bootstrap).
- **Deliverables:** PH diagnostics; C-index via bootstrap (preferred over naïve train/test split for small event counts).
- **Considerations:** Test results are evidence, not an automatic kill switch (§2.6 severity handling: minor → WARNING; manageable → stratify/refit; serious → fallback/suppress).
- **Verification:** Severity classification logic tested.

### Task 3.6 — Survival horizons
- **Objective:** Data-driven, configurable horizons.
- **Architecture refs:** §2.7.
- **Deliverables:** horizon availability logic (30/90/180 defaults) — AVAILABLE only when enough observed time, enough events around/after horizon, not dominated by uncertainty.
- **Considerations:** Never expose a horizon the data cannot support (§7 out-of-scope).
- **Verification:** Horizon availability tests.

### Task 3.7 — Kaplan-Meier fallback
- **Objective:** Honest fallback path.
- **Architecture refs:** §2.8.
- **Deliverables:** `churn_survival/node2/kaplan_meier.py` — global curve always produced; segment curves only for pre-approved categorical feature with min counts/events per segment; `risk_scores = null`; `model_type = "kaplan_meier"`, `model_status = "FALLBACK"`.
- **Considerations:** Never create tiny/noisy segments. KM segment curves are not individual risk scores.
- **Verification:** Fallback fixtures produce null risk_scores.

### Task 3.8 — Cold-start / insufficient observation
- **Objective:** Explicit "don't know".
- **Architecture refs:** §2.9.
- **Deliverables:** `customer_states` — customers with near-zero tenure and no usable features get `"not_enough_data"`, never forced into Low/Medium/High.
- **Verification:** Cold-start customer maps to `not_enough_data`.

### Task 3.9 — Deterministic interpretation layer
- **Objective:** Statistical results → fixed text.
- **Architecture refs:** §2.10.
- **Deliverables:** template rendering from coefficients (`exp(β)` → hazard ratio → deterministic sentence). LLM may polish tone later; never invent meaning.
- **Verification:** Interpretation strings match the §2.10 example exactly.

### Task 3.10 — Model artifact & versioning
- **Objective:** Fully reproducible artifact.
- **Architecture refs:** §2.11, §8.5.
- **Deliverables:** `churn_survival/node2/artifact.py` — joblib/cloudpickle model + JSON sidecar (version, training timestamp, dataset version, reference_date, features, coefficients, baseline, penalizer, n_customers, n_events, encoding scheme, validation metrics, assumption checks, horizon config).
- **Considerations:** Enables exact reproduction of any historical score.
- **Verification:** Artifact round-trip preserves all metadata.

### Task 3.11 — Status state machine
- **Objective:** Enforce status semantics.
- **Architecture refs:** §2.3.
- **Deliverables:** READY/WARNING/FALLBACK/INSUFFICIENT_DATA/FAILED transitions; `risk_scores` emitted only for READY/WARNING.
- **Considerations:** `risk_scores` is null whenever individual Cox scores can't be produced.
- **Verification:** Transition tests for each status.

### Task 3.12 — Node 2 test suite
- **Objective:** Coverage of eligibility, fit, status, artifact.
- **Architecture refs:** §8.9.
- **Deliverables:** `tests/node2/`.
- **Verification:** Coverage ≥ 90% on `node2/`.

---

## Phase 4 — Node 3 (Support Signal Extraction)

> **Extension — multi-source ingestion (X.com + Gmail).** Node 3 now accepts one
> or more external sources in addition to `support_data` via `node3/sources/`
> (`ExternalSource`, `MockXSource`/`XSource`, `MockGmailSource`/`GmailSource`),
> deterministic exact identity mapping (`config/identity_mapping/v1.json`),
> versioned source selection (`config/node3/sources_v1.json`), committed
> `mock_sources/` fixtures, and additive `source` provenance. External messages
> normalize into the existing `SupportThread` contract, so the §3.3–§3.8 logic and
> Node 4/Node 5 are unchanged. Live X/Gmail transports are deferred (no new
> dependencies); mock mode is the credential-free default. Full suite 956 passed,
> 1 live-LLM skipped; dataset-7 E2E and the golden proxy (κ 0.755 / 0.954)
> unchanged. See `docs/node3_multi_source_addendum.md`. Reported (not fixed):
> Node 5 renders Node 3 evidence as "reported in a support interaction" and does
> not expose the external source as a first-class field.
>
> **Multi-source adversarial-QA remediation (F-1…F-12; F-13 deferred).** Independent
> audit returned PASS WITH FINDINGS (2 HIGH, 5 MEDIUM, 3 LOW, 1 INFO; no CRITICAL).
> Fixed: untrusted-subject fence + HTML escaping and fail-closed message-id
> resolution (F-1/F-2); deterministic `processed_at` from `reference_date`
> (`node3/clock.py`, F-3); malformed payload → `SOURCE_DATA_INVALID` (F-4);
> per-source construction isolation `SOURCE_INIT_FAILED` (F-5); `enabled` selection
> gate + documented mode precedence (F-6/F-12); UTC-aware timestamp contract (F-7);
> support/external id-collision detection `ID_COLLISION` (F-8); hashed identity
> errors (F-9); length-independent secret redaction (F-10); per-source identity
> case rule + ambiguity fail-closed (F-11). Node 4/Node 5 unchanged. 1035 tests
> passing (242 in `tests/node3`), 1 live-LLM skipped; `node3/` coverage ~96%; ruff +
> `mypy schemas node3` clean; golden proxy unchanged; dataset-7 E2E green and CLI
> re-runs byte-identical. See addendum §17.

- **Status:** complete + QA-remediated; **multi-source form frozen (2026-09-16)** — Tasks 4.1–4.14 implemented plus multi-source ingestion (X.com + Gmail) and adversarial-QA remediation F-1…F-12 (F-13 deferred). 1035 tests passing (242 in `tests/node3`), 1 live-LLM skipped; `node3/` coverage ~96%; ruff + `mypy schemas node3` clean; CLI re-runs byte-identical.
  **Node 3 is frozen.** Do not modify Node 3 unless implementation of a later node exposes an actual contract defect or integration bug; add regression tests for any such fix.
  Dataset 7 E2E: `churn-survival node3 data/raw/dataset7_support_threads_messy.json --config dataset7` → bare CLI (thread-derived universe) **2680** customers; `--customers data/raw/dataset7_customers_messy.csv` → **4920** distinct IDs; 5685 threads processed, 45 quarantined (unsupported language), 8 near-duplicates collapsed (validated §3.3 rule; the 25 injected hints violate the 48h/subject criteria — see addendum §18.5). The **4550** valid-customer universe is the ground-truth set used by the golden harness.
  Golden-set proxy harness `scripts/eval_node3_golden.py`: κ(flag_type)=0.755, κ(signal_strength)=0.954, cancellation/renewal exact-match 1.000 — all §3.10 bars met.
  Adversarial QA findings resolved: N3-01 token budget enforced; N3-02 `duplicate_of` validated (survivor chosen by the §3.3 rule, not the hint direction); N3-03 CLI survives malformed rows; N3-04 `latest_interaction_at` from last message; N3-05 normalization-based near-exact dedup; N3-06 `llm_temperature` wired (≤0.2); N3-07 short-text language limitation documented; N3-08 offline billing/feature precision tightened.

### Task 4.1 — Inputs & config
- **Objective:** Accept the exact input contract.
- **Architecture refs:** §3.2.
- **Deliverables:** input validation (`customers`, `support_data`, config with all version fields: aggregation, vocabulary, preprocessing, prompt versions, `reference_date`, limits).
- **Considerations:** All limits (`lookback_days`, `max_threads_per_customer`, `max_messages_per_thread`, `max_tokens_per_customer`) are config-driven.
- **Verification:** Input contract tests.

### Task 4.2 — Deterministic preprocessing (base)
- **Objective:** Cleaning + windowing + language detection.
- **Architecture refs:** §3.3.
- **Deliverables:** `churn_survival/node3/preprocess.py` — filter to lookback window, remove pure system/automated messages, separate customer vs agent, exact/near-exact message dedup, deterministic language detection, mark unsupported languages, truncate to hard limits, preserve all `message_id`s and timestamps.
- **Considerations:** Customer-authored text is primary. All steps versioned.
- **Verification:** Preprocessing fixtures.

### Task 4.3 — Cross-channel near-duplicate collapse
- **Objective:** Merge duplicates without losing evidence.
- **Architecture refs:** §3.3.
- **Deliverables:** same `customer_id` + `created_at` within ±48h; cosine similarity on TF-IDF of (subject + first customer message) ≥ 0.82; AND normalized subject similarity ≥ 0.75 or shared key issue phrases; keep higher customer-token-count thread; set `duplicate_of`; **exclude collapsed threads from all counts**.
- **Considerations:** Collapsed threads excluded from `n_threads_in_window`, `n_messages_in_window`, `recurrence_count`.
- **Verification:** Duplicate fixture collapses correctly; counts exclude it.

### Task 4.4 — Language detection
- **Objective:** Supported/unsupported/unknown status.
- **Architecture refs:** §3.3, §3.12 (failure modes).
- **Deliverables:** language detection; `language_status` per thread.
- **Considerations:** Unsupported language → mark and skip/reduce confidence.
- **Verification:** Multi-language fixtures.

### Task 4.5 — ThreadSignals & RiskFlag extraction contract
- **Objective:** Define thread-level output.
- **Architecture refs:** §3.4.
- **Deliverables:** thread-level schema (sentiment, risk_flags, churn_language_detected, urgency_level, key_themes, meta with token counts + versions). RiskFlag carries `evidence` (message_id, text, timestamp) + `evidence_message_ids`.
- **Considerations:** This is the permanent evidence/audit layer; both layers persisted.
- **Verification:** Schema round-trip.

### Task 4.6 — Controlled vocabulary & governance
- **Objective:** Versioned flag taxonomy.
- **Architecture refs:** §3.4 (vocabulary + governance).
- **Deliverables:** `config/vocabulary.json` with hierarchy ranks (cancellation_intent=1 … competitor_mention=5, positive_feedback=—, other=residual); governance rule: `other` reviewed every 4 weeks or when >20% of flags; high-priority flags never silently moved to `other`.
- **Considerations:** Vocabulary is versioned (`vocabulary_version`); promotion of frequent patterns requires a version bump.
- **Verification:** Vocabulary version tracked in output meta.

### Task 4.7 — LLM thread-level extraction
- **Objective:** The only LLM usage in Node 3.
- **Architecture refs:** §3.9.
- **Deliverables:** `churn_survival/node3/llm_extractor.py` — thread-level only, forced structured output + Pydantic validation, temperature ≤ 0.2, hard per-customer token/message budget.
- **Considerations:** **No customer-level analytical LLM calls.** `summary` must be template-generated or null — never LLM-generated inside Node 3.
- **Verification:** LLM output validates against ThreadSignals; budget enforced.

### Task 4.8 — Aggregation: recency + recurrence
- **Objective:** Customer-level aggregation.
- **Architecture refs:** §3.8.1–3.8.2.
- **Deliverables:** `churn_survival/node3/aggregate.py` — `adjusted_strength = STRENGTH_SCORE[strength] * exp(-λ * age_days)` with `λ_default = 0.015`, `λ_persistent = 0.004` for cancellation_intent and renewal_or_contract_concern; per-flag_type selection via hierarchy+strength rule; record `recurrence_count`, temporal span, all evidence IDs; aggregated confidence = strongest instance's confidence.
- **Considerations:** Non-collapsed threads only.
- **Verification:** Recency and recurrence fixtures.

### Task 4.9 — evidence_quality_score & overall_signal_confidence
- **Objective:** Implement the locked formulas exactly.
- **Architecture refs:** §3.8.4, §3.8.5.
- **Deliverables:** `compute_evidence_quality_score` (length score `/12`, clarity keywords bonus 0.25, `0.75*length + clarity`, bounded) and `compute_overall_signal_confidence` (weights 0.20/0.20/0.20/0.15/0.25, volume sqrt, threads/3).
- **Considerations:** `no_data` → confidence 0.0. Round to 3 decimals deterministically.
- **Verification:** Match §3.8 example semantics exactly.

### Task 4.10 — Customer-level derivations + support_data_status
- **Objective:** Produce `CustomerSupportSignals`.
- **Architecture refs:** §3.8.3, §3.8.6.
- **Deliverables:** overall_sentiment (recency-weighted avg, label via fixed thresholds), urgency (max), escalation_signal (high urgency OR severity high with recurrence ≥2), churn_language (OR), signal_strength (hierarchy-first), `support_data_status` no/limited/sufficient.
- **Considerations:** `key_themes` informational only, never Node 4 input.
- **Verification:** Derivations table tests.

### Task 4.11 — Failure modes
- **Objective:** Handle every edge case explicitly.
- **Architecture refs:** §3.12.
- **Deliverables:** zero threads → `no_data`/0.0/none/null; only agent/system messages → limited/no signal; invalid LLM output → retry once → quarantine; unsupported language → mark/skip; duplicate collapse; missing `customer_id` → drop + error.
- **Verification:** One test per failure mode.

### Task 4.12 — Versioning meta
- **Objective:** Every run records versions.
- **Architecture refs:** §3.13.
- **Deliverables:** prompt_version, model_version, aggregation_version, vocabulary_version, preprocessing_version, lookback_days, reference_date in `meta`.
- **Verification:** Output meta assertion.

### Task 4.13 — Golden-set evaluation
- **Objective:** Quality gates before production.
- **Architecture refs:** §3.10.
- **Deliverables:** 150–300 double-annotated threads; acceptance: Cohen's κ ≥ 0.70 on `flag_type`, κ ≥ 0.65 on `signal_strength`, LLM exact-match ≥ 0.75 on cancellation_intent and renewal_or_contract_concern; ongoing spot-checks (30–50) + `other` bucket monitoring.
- **Considerations:** Re-evaluate golden set on every major prompt/model change.
- **Verification:** Evaluation script computes κ.

### Task 4.14 — Node 3 test suite
- **Objective:** Coverage.
- **Architecture refs:** §8.9.
- **Deliverables:** `tests/node3/` incl. LLM mocking (never call real LLM in tests).
- **Verification:** Coverage ≥ 90% on `node3/`.

---

## Phase 5 — Node 4 (Synthesis / Ranked Account List)

**Status: verified, frozen (2026-09-16)** (Tasks 5.1–5.12; 763 tests passing,
1 live-LLM skipped; `node4/` coverage 95–100%; ruff + `mypy schemas` clean;
Dataset 7 node1→node2→node3→node4 E2E and CLI smoke green). First QA findings
F-1…F-6 remediated (F-5 needed no code change), then a second independent
adversarial QA pass passed (27/27 contract-derived checks). Do not modify Node 4
unless a later node exposes an actual contract defect or integration bug.

### Task 5.1 — Configuration object
- **Objective:** Versioned decision config.
- **Architecture refs:** §4.2.
- **Deliverables:** `config/node4/v{version}.json` — `quantitative_weight`/`qualitative_weight` (must sum to 1.0), `agreement_bonus`, `risk_thresholds`, `quantitative_thresholds`, `confidence_weights`, `hierarchy_weights` (positive_feedback = 0.00), `strength_scores`, `strength_order`, `reference_date`; `ranking_version`, `threshold_version`, `critical_rules_version`.
- **Considerations:** All decision logic driven exclusively by this config + explicit rules.
- **Verification:** Weight-sum invariant test.

### Task 5.2 — Customer universe
- **Objective:** Union of both upstream node customers.
- **Architecture refs:** §4.3.
- **Deliverables:** `customer_ids = set(node2) | set(node3)`; every union member appears in `ranked_accounts` or `insufficient_data_accounts`.
- **Considerations:** Never drop a customer appearing in only one node.
- **Verification:** Universe test (single-source customers included).

### Task 5.3 — Quantitative normalization
- **Objective:** Convert Node 2 into [0,1].
- **Architecture refs:** §4.4.
- **Deliverables:** `survival_prob_90d` preferred (`1 - p`), else deterministic versioned `normalize_risk_score`; `None` if neither. **Missing quant score ≠ low risk.**
- **Verification:** Precedence + None tests.

### Task 5.4 — Qualitative scoring + recurrence bonus
- **Objective:** Node 3 signals → score.
- **Architecture refs:** §4.5.
- **Deliverables:** strongest-flag score (`hierarchy_weight * strength_score`), `no_data`/no flags → 0.0; recurrence bonus `min(0.20, 0.05 * max(0, max_recurrence - 1))`.
- **Considerations:** Do **not** reapply Node 3's recency decay. Positive feedback (0.00) can't reduce score.
- **Verification:** Recurrence bonus table tests (1→0, 2→+0.05, …, 5+→+0.20).

### Task 5.5 — Strongest qualitative signal tie-breaks
- **Objective:** Deterministic comparison.
- **Architecture refs:** §4.6.
- **Deliverables:** order by `strength_order`; tie → hierarchy weight; tie → recurrence count; tie → lexicographic flag_type.
- **Verification:** Tie-break fixtures.

### Task 5.6 — Combined score + agreement bonus
- **Objective:** Merge + bound.
- **Architecture refs:** §4.7.
- **Deliverables:** weighted sum; strong agreement (quant ≥ high AND qualitative strong) → +0.05; clamp to [0,1].
- **Considerations:** Agreement bonus can never directly create critical.
- **Verification:** Bound + agreement tests.

### Task 5.7 — Significant risk flag definition
- **Objective:** Critical-rule input predicate.
- **Architecture refs:** §4.8.
- **Deliverables:** significant = severity ∈ {medium, high} OR strength ∈ {moderate, strong}; positive_feedback never significant.
- **Verification:** Predicate tests.

### Task 5.8 — Risk level order + 4 critical rules
- **Objective:** Evaluate Critical → High → Medium → Low → Insufficient.
- **Architecture refs:** §4.9, §4.10, §4.12.
- **Deliverables:** Rule 1 (moderate/strong cancellation → critical); Rule 2 (strong cancellation + another significant flag, different type); Rule 3 (quant ≥ high + strong contract concern); Rule 4 (quant ≥ high + severity high with recurrence ≥ 2). Non-critical → combined-score thresholds.
- **Considerations:** **The combined score alone can never produce critical.** Rule 1 alone is sufficient.
- **Verification:** One test per rule + negative tests (weak-only never critical).

### Task 5.9 — Critical rule recording
- **Objective:** Every critical has a qualifying reason.
- **Architecture refs:** §4.11.
- **Deliverables:** reason_type ∈ {critical_cancellation_intent, critical_cancellation_plus_significant_flag, critical_high_quant_plus_contract_concern, critical_repeated_high_severity_plus_high_quant}; all applicable rules may be recorded; ≥1 always.
- **Verification:** Invariant test: every critical has a qualifying `primary_reason`.

### Task 5.10 — Confidence + missing upstream behavior
- **Objective:** Confidence independent of risk; handle missing nodes.
- **Architecture refs:** §4.14, §4.15.
- **Deliverables:** `QUANT_CONFIDENCE_BY_STATUS` (READY 1.00 / WARNING 0.70 / FALLBACK 0.45 / INSUFFICIENT_DATA 0.00 / FAILED 0.00); qualitative confidence = `overall_signal_confidence`; weighted combination, bounded, rounded to 3 dp. Node 3 missing → quant-only + warning; Node 2 missing → qual-only + warning; strong qualitative can still produce High/Critical.
- **Considerations:** Warnings: "Node 3 unavailable; quantitative-only synthesis used." / "Node 2 unavailable; qualitative-only synthesis used."
- **Verification:** Status→confidence mapping; missing-node fixtures.

### Task 5.11 — Ranking & sort
- **Objective:** Deterministic sequential ranks.
- **Architecture refs:** §4.18, §4.19, §4.20.
- **Deliverables:** sort by (1) risk level Critical>High>Medium>Low, (2) combined score desc, (3) cancellation_language_detected first, (4) strongest qual signal, (5) quantitative risk desc, (6) combined confidence desc, (7) customer_id asc; ranks from 1; insufficient-data rank = None.
- **Considerations:** Final ID key guarantees determinism.
- **Verification:** Sort-order + rank fixtures; rank-stability test (§4.27).

### Task 5.12 — Node 4 invariants tests
- **Objective:** Prove the §4.27 guarantees.
- **Architecture refs:** §4.27 (all 13), §4.26 (failure handling), §4.28 (versioning).
- **Deliverables:** `tests/node4/` — determinism, universe, rank stability, explanation independence, LLM failure, Node 3/Node 2 failure, critical rule requirement, critical score protection, positive feedback protection, no-data protection, evidence traceability, rank independence. Malformed upstream → error, continue.
- **Verification:** Full invariant suite green.

---

## Phase 6 — Node 5 (Client-Facing Report)

**Status: verified, frozen (2026-09-16)** (Tasks 6.1–6.12 implemented across three milestones A/B/C;
868 tests passing, 1 live-LLM skipped; `node5/` coverage 93%; ruff + `mypy schemas` clean;
Dataset 7 node1→node2→node3→node4→node5 E2E green). Node 5 is a presentation layer: it copies
Node 4 decisions verbatim, never re-sorts/recalculates, and uses `Node3Output` strictly as an
evidence lookup source. The LLM is optional explanation polish only with a mandatory
deterministic fallback. Decisions D-U1…D-U11/D-REC/D-VAL/D-ORDER/D-RENDER are locked in
`docs/phase6_node5_implementation_plan.md`. PDF rendering is deferred (no approved dependency);
JSON + dependency-free HTML are the core renderers. Node 3 and Node 4 were not modified. Note:
the dataset-7 `node5_trap_oracle` disagrees with the real Node 4 output for 20/40 trap customers
(traps 001/002) — an oracle/Node 4 discrepancy Node 5 surfaces, not fixes.

**Adversarial-QA remediation (2026-09-16):** all 9 audit findings fixed with regression
coverage (F-1 customer-bound evidence + mismatch errors; F-2 explicit allowed-facts explanation
validation; F-3 non-empty/unambiguous provenance blocks publication; F-4 malformed `top_flags`
errors; F-5 duplicate/cross-list IDs rejected; F-6 `may` date false positive; F-7 HTML
quantitative/support sections + PDF deferral documented; F-8 display-name bounds; F-9
`reason_explanations` validated-not-surfaced + dead index field removed). 899 tests passing;
`node5/` coverage 94%. **Node 5 is frozen** — do not modify unless a later phase exposes an
actual contract defect or integration bug, with regression tests for any such fix.

### Task 6.1 — Input contract
- **Objective:** Accept validated Node 4 output.
- **Architecture refs:** §5.3.
- **Deliverables:** input validation of `node4_output` + optional `customer_data` (name/segment/industry/owner only) + config (`report_version`, `prompt_version`, `include_*` flags, `max_*`, `language`).
- **Considerations:** Never use `customer_data` to calculate risk. Fall back to `customer_id` when context missing.
- **Verification:** Input tests.

### Task 6.2 — Node 4 validation
- **Objective:** Fail safely before generating.
- **Architecture refs:** §5.4.
- **Deliverables:** `churn_survival/node5/validation/node4_validator.py` — 12 checks (exists, schema, valid IDs, risk level enum, score/confidence in [0,1], critical has qualifying reason, evidence refs valid, summary stats match lists, sequential ranks, no duplicate in main list, no cross-list membership).
- **Considerations:** Never silently continue on failure — record validation errors.
- **Verification:** Each check tested.

### Task 6.3 — Deterministic sections
- **Objective:** Complete report without AI.
- **Architecture refs:** §5.6, §5.7, §5.8, §5.23, §5.24.
- **Deliverables:** `build_executive_summary()` (numbers from Node 4 only), `build_risk_distribution()` (counts), `build_customer_report()` (accounts in Node 4 order — **never re-sort**), `build_data_quality_section()`, `build_methodology_section()`.
- **Considerations:** Exec-summary numbers are deterministic; LLM may polish wording, never the numbers.
- **Verification:** Section outputs match Node 4 inputs exactly.

### Task 6.4 — Evidence representation + privacy modes
- **Objective:** Traceable, privacy-controlled evidence.
- **Architecture refs:** §5.11, §5.12, §5.13.
- **Deliverables:** `churn_survival/node5/report/evidence.py` — node2/node3 references; config-driven modes (disabled / summary only / short quote / full evidence; default short quote/paraphrase); underlying references always available internally.
- **Considerations:** Never invent a quotation; quotations must come from Node 3 evidence.
- **Verification:** Evidence mode tests.

### Task 6.5 — LLM explainer (structured)
- **Objective:** Explain-only contract.
- **Architecture refs:** §5.15, §5.16.
- **Deliverables:** `churn_survival/node5/llm/explainer.py` + schemas — receives only computed risk level, score, confidence, reasons, approved evidence refs + text; returns structured JSON (`headline`, `summary`, `reason_explanations`), Pydantic-validated.
- **Considerations:** LLM must never return/regenerate `risk_level`, `rank`, `score`.
- **Verification:** Mocked LLM tests; forbidden-field rejection.

### Task 6.6 — Explanation validation
- **Objective:** Reject contradictions.
- **Architecture refs:** §5.28.
- **Deliverables:** post-LLM check: no different risk level/rank/score, no unsupported numbers/facts/risk factors/evidence/recommendations. E.g. Node 4 says `cancellation_intent = false` + LLM claims cancellation → reject.
- **Considerations:** One of the most important safeguards.
- **Verification:** Contradiction fixtures rejected.

### Task 6.7 — Deterministic fallback
- **Objective:** Report survives LLM failure.
- **Architecture refs:** §5.17.
- **Deliverables:** template fallback on fail/timeout/invalid JSON/schema violation/unsupported claims; `llm_failures` counted.
- **Considerations:** **LLM failure must never prevent report generation.**
- **Verification:** Failure-mode tests for each failure type.

### Task 6.8 — Recommended actions
- **Objective:** Auditable deterministic mappings.
- **Architecture refs:** §5.18, §5.19.
- **Deliverables:** `config/action_rules.json` — versioned `ACTION_RULES` (cancellation_intent → contact account, etc.); selection by priority order; never invent a recommendation for a nonexistent reason.
- **Considerations:** Gated by `include_recommendations`. Versioned for auditability.
- **Verification:** Priority + gating tests.

### Task 6.9 — Final consistency checks
- **Objective:** Do-not-publish gate.
- **Architecture refs:** §5.29.
- **Deliverables:** pre-publish checks: rank/risk/score/confidence equal Node 4, counts match, critical customers supported by critical rules, insufficient-data separated, evidence refs valid, versions recorded.
- **Considerations:** Any mandatory check failure → do not publish.
- **Verification:** Check harness tests.

### Task 6.10 — Report metadata
- **Objective:** Reproducibility.
- **Architecture refs:** §5.25.
- **Deliverables:** `report_version`, node2_model_version, node3_signal_version, node4 ranking/threshold/critical-rules versions, prompt/llm versions, `reference_date`, `generated_at`.
- **Verification:** Metadata completeness test.

### Task 6.11 — Rendering (JSON / HTML / PDF)
- **Objective:** Three outputs from one object.
- **Architecture refs:** §5.27, §5.30, §5.31.
- **Deliverables:** `churn_survival/node5/rendering/{json,html,pdf}.py` — all render the same validated report object; layout per §5.31; PDF only after JSON+HTML stable.
- **Considerations:** No separate logic for PDF vs HTML.
- **Verification:** Render determinism + layout snapshot tests.

### Task 6.12 — Node 5 invariants + failure-mode tests
- **Objective:** Prove §5.34 + §5.33.
- **Architecture refs:** §5.33, §5.34, §5.35 (component split), §5.36 (implementation order).
- **Deliverables:** `tests/node5/` — 12 invariants; test set covering normal/conflicting/LLM-failure/data-problem cases; package layout per §5.35.
- **Verification:** Full invariant + failure suite green.

---

## Phase 7 — Orchestration

**Status: complete (2026-09-27).** Implemented as a deterministic, plain-Python
state machine instead of LangGraph — architecture §6.5 permits "LangGraph **or
equivalent**", and no graph framework is needed. `orchestration/` sequences the
frozen nodes with explicit stop conditions, a human mapping-confirmation gate,
and a resumable/round-trippable run state. Decisions D-O1…D-O6 are locked in
`docs/phase7_orchestration_plan.md`. Dataset 7 node1→node5 E2E green through
`churn-survival run`; ruff + `mypy schemas` clean.

### Task 7.1 — Router + human-confirmation graph
- **Objective:** Graph around the deterministic core.
- **Architecture refs:** §0.1, §1.6.
- **Deliverables:** `orchestration/routing.py` (LLM-free routing node) and
  `orchestration/mapping.py` (`MappingGate` protocol + `persist_confirmed_mapping`).
  Human confirmation is the final safety gate; the graph never bypasses it and
  never auto-confirms.
- **Verification:** `tests/orchestration/test_routing.py`,
  `tests/orchestration/test_mapping_confirmation.py` (stubbed human decision +
  conversation resume).

### Task 7.2 — Node sequencing
- **Objective:** Node1 → 2 → 3 → 4 → 5 with explicit stop conditions.
- **Architecture refs:** §1.2 (Node 1 failure stops batch), §3.x/§4.x/§5.x boundaries.
- **Deliverables:** `orchestration/graph.py` (`run_pipeline` / `resume_pipeline`);
  Node 1 total validation failure short-circuits; partial state is retained on a
  node exception. Statistical steps remain plain Python functions called by the
  sequencer — never in graph internals.
- **Verification:** `tests/orchestration/test_sequencing.py`,
  `tests/orchestration/test_stop_conditions.py`,
  `tests/orchestration/test_e2e_dataset7.py`.

### Task 7.3 — Graph tests
- **Objective:** Correctness of routing + ordering.
- **Architecture refs:** §6 (principles).
- **Verification:** coverage on `orchestration/` (state, routing, mapping, graph,
  CLI); determinism assertions on Node 4/5 output.

---

## Phase 8 — Persistence & API

**Status: complete (2026-09-27)** — Tasks 8.1–8.3 implemented. Decisions D-P1…D-P13
are locked in `docs/phase8_persistence_api_plan.md`. 1130 tests passing, 1 live-LLM
skipped; ruff + `mypy schemas` clean; dataset-7 E2E persisted and byte-identical
across two runs. Key outcomes:
- **Routing-inclusive content-addressed `run_id`** (`orchestration/identity.py`,
  decision-free): the mapping registry is part of run identity, so confirming a
  mapping yields a new run and the stopped run survives as audit.
- **Run store + load-bearing SQLite index** (`orchestration/persistence.py`,
  `orchestration/index.py`): `runs/<run_id>/{state,summary,node1..4,node5}.json` +
  `report.html` + `index.sqlite`; self-heals from disk; legacy rows surface
  `unknown_pre_migration`.
- **FastAPI serving** (`api/`): read endpoints never recompute (contract-tested);
  `POST /runs` triggers one async run (202 + poll) with an explicit status matrix
  and `force`; auth/write gates (writes off by default, enabling requires a key);
  mapping draft/confirm through the orchestration gate only.
- **Hybrid retention GC** (`orchestration/gc.py`, `churn-survival gc`): count-based
  for artifacts/runs, TTL-based for pending states + unconfirmed drafts, never
  confirmed mappings.

### Task 8.1 — Artifact & mapping persistence
- **Objective:** File-based versioned storage.
- **Architecture refs:** §8.5.
- **Deliverables:** joblib/cloudpickle model + JSON sidecar (`§2.11`); versioned mapping configs in `config/mappings/`; clear directory structure under `models/`; optional thin SQLite metadata index.
- **Considerations:** Add MLflow/registry only when file-based approach hurts.
- **Verification:** Persistence round-trip tests.

### Task 8.2 — FastAPI serving
- **Objective:** Expose scored/synthesized output.
- **Architecture refs:** §8.7.
- **Deliverables:** `churn_survival/api/` — endpoints serving Node 4/5 outputs; uvicorn.
- **Considerations:** API layer never recomputes decisions — it serves stored artifacts/reports.
- **Verification:** API contract tests.

### Task 8.3 — Full-pipeline E2E
- **Objective:** One run through all nodes.
- **Deliverables:** `tests/e2e/` — raw input → canonical → model → signals → ranked list → report (PDF/HTML/JSON).
- **Verification:** Determinism across two full runs.

---

## Phase 9 — Production Hardening

### Task 9.1 — Observability
- **Objective:** Structured logging everywhere; optional OpenTelemetry.
- **Architecture refs:** §8.8.
- **Considerations:** Keep it simple unless already using OTel.
- **Verification:** Sample logs include node + versions.

### Task 9.2 — Golden sets
- **Objective:** Node 3 quality gates in production.
- **Architecture refs:** §3.10.
- **Verification:** κ thresholds enforced in CI gate.

### Task 9.3 — Reproducibility audit
- **Objective:** Prove bit-identical reruns.
- **Architecture refs:** §6, §4.27.
- **Verification:** Audit script reruns a past dataset+config pair and diffs outputs.

---

## Appendix A — Architecture → Roadmap Cross-Map

| Architecture section | Implemented by |
|---|---|
| §0.1 Router Decision Rule | 2.1, 2.2, 2.3, 2.5, 7.1 |
| §1.2 Node 1 output contract | 1.1, 2.8 |
| §1.3 Canonical schema / field rules | 1.1, 1.4, 2.6, 2.11 |
| §1.4 Adapter contract | 2.1, 2.6 |
| §1.5 Deterministic adapters | 2.4 |
| §1.6 LLM mapping report | 1.1, 2.2, 2.5, 7.1 |
| §1.7 Validation gates | 2.7, 2.8, 2.12 |
| §1.8 Feature gate | 2.9 |
| §2.2 Fit/score separation | 3.2 |
| §2.3 Model status | 3.11 |
| §2.4 Eligibility | 3.1 |
| §2.5 Multicollinearity | 3.4 |
| §2.6 CoxPH path | 3.3, 3.5 |
| §2.7 Horizons | 3.6 |
| §2.8 KM fallback | 3.7 |
| §2.9 Cold start | 3.8 |
| §2.10 Interpretation layer | 3.9 |
| §2.11 Model artifact | 1.1, 3.10, 8.1 |
| §2.12 Node 2 output | 1.1 |
| §3.2 Inputs | 1.1, 4.1 |
| §3.3 Preprocessing | 4.2, 4.3, 4.4 |
| §3.4 ThreadSignals / vocabulary | 1.1, 4.5, 4.6 |
| §3.5 CustomerSupportSignals | 1.1, 4.10 |
| §3.7 Strength mapping | 1.2 |
| §3.8 Aggregation rules | 4.8, 4.9, 4.10 |
| §3.9 LLM rules | 4.7 |
| §3.10 Evaluation | 4.13, 9.2 |
| §3.12 Failure modes | 4.11 |
| §3.13 Versioning | 4.12 |
| §4.2 Config | 5.1 |
| §4.3 Inputs / universe | 5.2 |
| §4.4 Quant normalization | 5.3 |
| §4.5 Qualitative scoring | 5.4 |
| §4.6 Strongest signal | 5.5 |
| §4.7 Combined score | 5.6 |
| §4.8 Significant flag | 5.7 |
| §4.9–4.12 Risk levels + critical rules | 5.8, 5.9 |
| §4.13 Insufficient data | 5.2, 5.8 |
| §4.14–4.15 Confidence / missing nodes | 5.10 |
| §4.16 Primary reasons | 5.9 |
| §4.18–4.20 Ranking | 5.11 |
| §4.26 Failure handling | 5.12 |
| §4.27 Invariants | 5.12 |
| §4.28 Versioning | 5.12 |
| §5.3 Input | 6.1 |
| §5.4 Node 4 validation | 6.2 |
| §5.6–5.8 Sections | 6.3 |
| §5.11–5.13 Evidence | 6.4 |
| §5.15–5.16 LLM | 6.5 |
| §5.17 Fallback | 6.7 |
| §5.18–5.19 Actions | 6.8 |
| §5.25 Metadata | 6.10 |
| §5.27–5.30 Generation / formats | 6.11 |
| §5.28 Explanation validation | 6.6 |
| §5.29 Consistency checks | 6.9 |
| §5.33–5.34 Tests / invariants | 6.12 |
| §6 Principles | all phases |
| §8.5 Persistence | 8.1 |
| §8.6 Config/secrets | 0.4 |
| §8.7 API | 8.2 |
| §8.8 Observability | 0.5, 9.1 |
| §8.9 Testing | 0.6, every node suite |
| §8.10 Project structure | 0.2 |
| §8.11 Dependencies | 0.3 |

## Appendix B — `.env.example`

```dotenv
# Runtime
LOG_LEVEL=INFO
REFERENCE_DATE=2026-08-15

# LLM (provider-agnostic)
LLM_PROVIDER=anthropic        # anthropic | openai | none
LLM_API_KEY=
LLM_MODEL=

# Paths
MODEL_DIR=models/
RAW_DATA_DIR=data/raw/
PROCESSED_DATA_DIR=data/processed/
CONFIG_DIR=config/

# Pipeline behavior
DEFAULT_LOOKBACK_DAYS=365
MAX_THREADS_PER_CUSTOMER=50
MAX_MESSAGES_PER_THREAD=100
```

---

## Suggested Build Order Summary

```
Phase 0 (scaffolding)          → always first
Phase 1 (schemas)              → before any node
Phase 2 (Node 1)               → then Node 2 (3.x) → Node 3 (4.x)
Phase 5 (Node 4)               → after Node 2 + Node 3
Phase 6 (Node 5)               → after Node 4
Phase 7 (orchestration)        → wraps 0–6
Phase 8 (persistence + API)    → when serving is needed
Phase 9 (hardening)            → continuous
```

Each phase must leave the repository in a green, testable state. The architecture document remains the authority for any ambiguity not resolved here.
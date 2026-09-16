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
- **LLM mapping path hardened (router/adapters).** `apply_transformation` now
  fails loudly on any transformation outside the audited whitelist (exact-match
  ops: `identity`, `str.strip()`/`strip`, `to_float`/`float`, `to_int`/`int`,
  `parse_date`, `months_before(reference_date)`, `snapshot_end(reference_date)`,
  `row_number`, `map({...})` dict literals) — the old silent pass-through for
  unknown ops is gone (`is_allowed_transformation` /
  `validate_transformation`). `validate_mapping_report` (called by both
  `generate_mapping_report` and `confirm_and_persist`) rejects: non-whitelisted
  transforms, `row_number` outside `customer_id`, `core.<key>` targets not in
  the `CoreFeatures` union, and any `target_field` that is neither an identity
  field nor `core.<union-key>` (extras must go through `suggested_extra_features`).
  The LLM system prompt was tightened with strict rules + two few-shot examples
  (leakage/decoy demotion, no-force-fit-core rule). Live eval harness
  `scripts/eval_llm_mapping.py` (7 messy datasets incl. Dataset 6) passes all
  success criteria with the configured model — 100% schema-valid first try,
  100% whitelisted transforms, 0 leakage into core, 100% strict-validation pass
  (confirmed across 3 live runs). 442 tests passing; ruff + `mypy schemas`
  clean; the 6 confirmed mapping configs all pass strict validation; Node 1
  dataset6 E2E unchanged (`PARTIAL accepted=5000 rejected=25`).
- **6th synthetic dataset onboarded — `dataset6_saas_churn_messy` (5,025 raw rows, 400 events).**
  Generated by `scripts/generate_dataset6.py` (fixed seed 42, fully deterministic;
  re-runs byte-identical) into `data/raw/dataset6_saas_churn_messy.csv` + a
  `data/ground_truth/dataset6_saas_churn_ground_truth.json` companion (per-row
  truth, intended effect directions, edge-case labels, expected Node 1 outcome).
  A messy SaaS export (awkward headers, mixed date formats, mixed churn
  representations, decoy/noise/leakage columns) that no deterministic adapter
  matches, forcing the mapping path; confirmed `config/mappings/map_*.json` +
  `config/node1/vdataset6.json` (approved core keys: `plan_tier`,
  `contract_length_months`, `usage_frequency`, `support_tickets_90d`). This adds
  `support_tickets_90d` to the `CoreFeatures` deployment-union
  (`schemas/canonical.py`) — the documented union-addition act (docs/onboarding.md
  step 4). 25 deliberately-invalid rows are injected (future dates, duplicate IDs,
  non-binary status, missing usage) and correctly quarantined:
  `churn-survival node1 data/raw/dataset6_saas_churn_messy.csv --config dataset6`
  → `PARTIAL accepted=5000 rejected=25`; `node2 … --config dataset6` → `cox_ph`
  (4994 scored, 400 events, horizons [30,90,180] AVAILABLE) recovering the
  intended directions (`plan_tier_starter` HR>1, `contract_length_months` HR<1,
  `usage_frequency` HR<1, `support_tickets_90d` HR>1; the plan_tier oracle claim
  is starter-only — pro is confounded, see F-5). 415 tests passing;
  ruff + `mypy schemas` clean.
- **Independent adversarial QA (Node 1 + Node 2) — done.** A from-scratch QA
  pass (fixtures built only from `architecture.md`/`ROADMAP.md`, no repo tests
  read) found 5 issues; all 5 fixed (report-only exercise, fixtures deleted):
  - **F-1 (Medium, Node 1) — FIXED.** Blank CSV cells become the literal string
    `"nan"` for string core features and `customer_id` (pandas NaN → `str(NaN)`
    in the clean_csv adapter's string coercion), so missing string cores were
    accepted as `"nan"` instead of quarantined (`CORE_MISSING`), and §1.7
    missingness was undercounted for string columns. Numeric columns unaffected.
    **Fix:** `coerce_string` (adapters/_table.py) now maps every pandas NA
    sentinel (`float('nan')`, `pd.NA`, `NaT`, numpy floats) to `None` via
    `pd.isna` — the literal source string `"nan"` is preserved; the mapping
    adapter's `_coerce_for_key` does the same for `core.*` targets that survive
    an `identity`/`str.strip()` op as NaN; `_core_value_missing` (validation)
    counts non-finite floats as missing as defense-in-depth. Regression tests:
    blank `plan_tier` → `CORE_MISSING` + missingness (1/4 ≤ 30% batch passes),
    blank `customer_id` → `None` (never `"nan"`), blank numeric cells still
    `None`. All five real-data E2Es unchanged (`nan_pollution=0` in accepted
    records).
  - **F-2 (Medium, Node 1) — FIXED.** The §1.7 missingness batch gate was
    computed over every input row *including* records rejected for unrelated
    reasons, so a few invalid rows could turn a `PARTIAL` batch into a hard
    `FAILED` batch and discard the healthy records. **Fix:** the gate now
    evaluates over the *evaluable subset* — records with no errors other than
    `CORE_MISSING` — so unrelated-invalid rows are excluded from both the
    numerator and denominator (`missing_denominator` in
    `node1/validation.py`; `_column_missingness` takes `n_evaluable`, skips on
    zero, and the error message states the evaluable denominator). Regression
    tests: 8 healthy + 2 `CORE_MISSING`-only + 3 unrelated-invalid (bad date +
    blank `plan_tier`) rows → 5/13=38% old (FAILED) vs 2/10=20% new (PARTIAL,
    8 healthy accepted, no `COLUMN_MISSINGNESS`); and the no-evaluable edge
    (missingness gate skipped, batch still fails via `n_accepted == 0`). All
    five real-data E2Es unchanged (dataset6 5025 rows → 5015 evaluable, all
    fractions ≤0.2%).
  - **F-3 (Low, Node 1) — FIXED.** The extreme-outlier tenure-sanity guard
    (`max_extreme_outlier_ratio: 0.10`) was near-inert due to std masking: a
    single extreme value inflates the standard deviation and hides itself, so
    with 12 records and one 75× outlier the batch still passed. **Fix (Option A,
    robust rule):** `_tenure_sanity` now uses median + MAD (immune to that
    masking) instead of mean/std — extreme tenures are flagged when
    `abs(t - median) > outlier_mad_factor * scale`, where `scale` is the MAD (or,
    when MAD is 0 from a majority-tied batch, a conservative ~2×-median floor so
    legitimate near-tied spreads aren't misread as corruption). The misnamed
    `outlier_std_factor` became `outlier_mad_factor`, the default
    `max_extreme_outlier_ratio` dropped 0.10 → 0.05 so a single genuine extreme
    outlier in a small batch (1/12 = 8.3%) actually trips the default config, and
    `validation_version` bumped to 1.0.1 across all node1 configs. Zeros guard
    untouched. Regression test: 12 records (11×8d + 1×600d) with the default
    config → `TENURE_SANITY`, batch rejected; the previous mean/std rule gave
    0.000 ratio. All five real-data E2Es unchanged (robust ratio = 0.0000 on
    every dataset with the default factor — ten-fold headroom under the 0.05
    threshold).
  - **F-4 (Low, Node 1/LLM provenance) — FIXED.** `llm_model_used` in the mapping
    report is now recorded from the actual client configuration (`LlmClient.model`
    / `LLM_MODEL`), overriding any self-reported model name in the LLM's JSON
    payload, so versioned provenance metadata is reliable. Regression test: payload
    self-reports a different model; the stored `llm_model_used` matches the
    configured client model end-to-end through `confirm_and_persist`.
  - **F-5 (Low, fixture/docs) — FIXED.** The ground-truth `directions` block is
    now an honest oracle: it no longer claims an adjusted higher hazard for
    `plan_tier.pro` (`effect.pro` = `no_reliable_adjusted_claim` + a confounding
    note). The generative DGP coefficient (`pro=0.35`) is retained as a factual
    parameter, and the generator still asserts only the univariate ordering
    (`starter > pro > enterprise`), which holds. The fitted Cox model's reversed
    estimate for pro (HR≈0.80) is expected behaviour, not a pipeline bug.
- **7th synthetic corpus onboarded — `dataset7_churn_diagnostic` (5,000 raw rows, 511 events, 5,735 threads).**
  A deterministic end-to-end diagnostic dataset for Nodes 1→5, generated by
  `scripts/generate_dataset7.py` (master seed `2137457950`, reference date
  `2026-08-15`) and self-validated by `scripts/validate_dataset7.py` (40 §33
  checks). Artifacts: `data/raw/dataset7_customers_messy.csv` (mixed formats:
  4 date layouts, 5 churn representations, 78-column-style extras incl.
  `Legacy Flag`/`Decoy A`/`Decoy B`), `data/raw/dataset7_support_threads_messy.json`
  (email/chat/phone/twitter 55/25/15/5 incl. `emial`/`chatt` typos; 25
  cross-channel duplicate pairs; 45 unsupported-language threads es/de/fr; 26
  Node 3 support cohorts × 14 segments incl. R1–R4 rules), and
  `data/ground_truth/dataset7_ground_truth.json` (per-row truth, scenario oracles
  for Nodes 2/3/4, trap oracle, DGP params, cohort counts). **Determinism proven:**
  re-runs are byte-identical (golden SHA-256 pinned in `tests/dataset7/test_dataset7.py`).
  DGP is Cox-Weibull with frailty: β_starter=0.85/β_pro=0.25/β_contract=-0.06/
  β_usage=-0.18/β_tickets=0.10, λ0 locked 36.0 mo (attempt 90, 511 events in-band,
  no bisection); `support_tickets_90d` is *recomputed from actual in-window threads*
  after windows are known (resolves the tenure<90d circularity; planned value stays
  the DGP covariate in `generator.coefficients`, 41 customers reconciled).
  Onboarding is config-driven: `config/node1/vdataset7.json` (approved cores
  `plan_tier`/`contract_length_months`/`usage_frequency`/`support_tickets_90d`) +
  confirmed `config/mappings/map_20260820T000000Z.json` (adapter
  `mapping:e6bfd1745c54`). `churn-survival node1 data/raw/dataset7_customers_messy.csv --config dataset7`
  → `PARTIAL accepted=4550 rejected=450` with gates WINDOW_ORDER 150 / FUTURE_LEAKAGE 50 /
  EVENT_OBSERVED 40 / UNIQUE_ID 80 / CORE_MISSING 130 (incl. the 30 INVALID_PLAN rows,
  which have no plan vocabulary gate and fall through to `CORE_MISSING` via a blank usage cell);
  the 450 invalid rows exercise 8 taxonomy codes (future dates, duplicate IDs, non-binary
  status, missing cores, impossible/negative tenure, invalid plan). `node2 … --config dataset7`
  → `cox_ph` WARNING (4550 scored, 511 events, horizons 30/90/180; model_version
  `bec0e6091e6b1743` deterministic, c-index 0.76) recovering `contract_length_months` HR<1,
  `usage_frequency` HR<1, `support_tickets_90d` HR>1; plan_tier is **stratified** (Node 2's
  PH-violation refit stratifies on the only categorical feature — the serious violator is
  numeric `support_tickets_90d`), so the truth `directions.plan_tier` honestly claims
  `no_reliable_adjusted_claim` (per-stratum baselines; univariate starter>pro>enterprise
  asserted). Also: `%Y.%m.%d` added to the adapter date-formats whitelist (dataset7 uses a
  dot-separated layout). 459 tests passing; ruff + `mypy schemas` clean.
- **Dataset 7 upgraded to spec v1.2 (missingness, time-varying PH, traps) — done.**
  `scripts/generate_dataset7.py` (v1.1) now emits *literal* §11 missingness
  (blank cells: usage 3.0% MAR, tickets 10.2% enterprise-MAR, contract 21.1%
  monthly-starter MNAR — `MISSINGNESS_SEED=MASTER_SEED+7`), §5.3 FAILED
  corruption markers on rows 300–349, a §31 time-varying-PH cohort (exactly 50
  customers in `[PH_LO,PH_HI]=[350,400]`, `support_tickets_90d` coefficient cut
  to `BETA_USAGE*0.5` after 180 days), quant-only (CUST-0771..0970, scores
  0.9269–0.9667) / qual-only-strong (CUST-0201..0225, critical +
  `missing_quantitative_data`) cohorts, and §24 traps whose oracle equals the
  expected synthesizer output. `scripts/validate_dataset7.py` is now **58 checks**
  (new 41–58) and passes; `_bisect_lambda0` bug fixed (inverted bracket looped
  forever → converges to largest λ with count ≥ min; λ0=61.605846 mo, 420 events,
  attempt 100). Golden SHA-256 pinned in tests: CSV
  `BB7ADC38…8D8FBF979`, threads `A4A274C7…EB4DE3`, truth `AF30A2AA…11F2011`
  (byte-identical across runs). **Documented deviations from the plan (see
  `docs/dataset7_addendum_v1.2.md`):** (1) Node 1 passthrough (architecture §1.7
  Amendment v1.2, `allow_missing_core_passthrough` in `vdataset7.json`) rescues
  *all* blank-core rows including the 130 taxonomy rows → `PARTIAL accepted=4680
  rejected=320` (not 4550/450; `missingness_passthrough` contract 283 / tickets
  148 / usage 205); (2) Node 2 `_prepare` builds specs from the *scored*
  complete-case subset (phantom plan values from rescued rows no longer create
  all-zero CoxPH columns) and the PH check returned **severity=none → no
  stratified refit** (strata_used=None): unstratified cox_ph, n_customers=4055,
  n_events=353, c_index=0.7606, `plan_tier_starter` HR>1 recovered,
  `plan_tier_pro` fitted sign disagrees with DGP (+0.25) → truth honestly claims
  `plan_tier_recoverability="partial"`, pro `no_reliable_adjusted_claim`;
  (3) `encoding_scheme` guards empty categorical specs (all-excluded dataset →
  `INSUFFICIENT_DATA`, no IndexError). Router fixtures added:
  `data/raw/dataset7_customers_modern.csv` (canonical headers → `clean_csv`) and
  `dataset7_customers_german.csv` (German headers → `UnmappedFormatError`).
  **467 tests passing; coverage 97%; ruff + `mypy schemas` clean.**
- **Phase 4 (Node 3 — Support Signal Extraction) — complete.** Tasks 4.1–4.14 done:
  `config/vocabulary.json` + frozen `VocabularyConfig` + `load_vocabulary`,
  versioned `config/node3/v1.json` (arch default lookback 365) and
  `config/node3/vdataset7.json` (lookback 1095 — Dataset 7 observation windows
  span up to 3 years), `node3/` (`vocabulary`, `preprocess`, `llm_extractor`,
  `aggregate`, `node`), `pipeline/main.py` dispatches `node3`, and
  `scripts/eval_node3_golden.py`. Contracts extended (architecture §3.2/§3.5):
  `SupportThread` gained optional `language`/`duplicate_of` input annotations,
  `CustomerSupportSignalsMeta` gained `reference_date` (§3.13), and
  `ThreadSignals` gained `latest_message_at`. LLM extraction reuses
  `router.LlmClient` (now with an optional `temperature`) with a deterministic
  offline keyword extractor (`LLM_PROVIDER=none`); unsupported language →
  quarantine. Locked §3.8.4/§3.8.5 formulas implemented verbatim. **575 tests
  passing, 1 live-LLM skipped; `node3/` coverage 95%; ruff + `mypy schemas node3`
  clean.** Dataset 7 E2E (offline): bare CLI (thread-derived universe) = **2680**
  customers; `--customers data/raw/dataset7_customers_messy.csv` = **4920**
  distinct IDs; 5685 threads processed, 45 failed (unsupported language), 8
  duplicates collapsed. The **4550** valid-customer universe is the ground-truth
  set used by the golden harness, not the bare CLI. Golden proxy
  κ(flag_type)=0.755, κ(strength)=0.954, cancellation/renewal exact-match 1.000 —
  all §3.10 bars met.
- **Node 3 adversarial-QA remediation — done.** An independent QA pass found
  defects; all confirmed findings fixed (see `ROADMAP.md` Phase 4 for the
  finding-by-finding disposition). Decisions locked:
  - **`max_tokens_per_customer` is enforced** as a hard cumulative cap over
    customer-authored tokens (the LLM prompt content). Threads are considered
    newest-first, whole threads kept while they fit, and the first overflowing
    thread is dropped with all older ones (`TOKEN_BUDGET_EXCEEDED` error +
    warning). `ThreadSignalsMeta.n_tokens_sent` now counts customer-authored
    tokens (what is actually sent).
  - **`duplicate_of` input hints are validated** against the same §3.3 predicate
    as automatic detection (48h + cosine ≥ 0.82 + subject ≥ 0.75/shared phrase);
    a hint that fails is ignored. An accepted hint only asserts the pair is a
    duplicate — the survivor is always chosen by the §3.3 rule
    (`_pick_survivor`: higher customer-token count), never by the hint's
    direction. Consequence: Dataset 7 collapses **8** pairs, not the addendum's
    25 — only **1 of 25** injected pairs is within 48h, so the 25 are a
    dataset/oracle inconsistency, not Node 3 behaviour (see addendum appendix).
  - **CLI no longer pre-validates every entry**: malformed rows are dropped by
    `preprocess_threads` with structured errors (`INVALID_THREAD` /
    `MISSING_CUSTOMER_ID`) while valid rows continue; top-level config/file
    failures still exit non-zero.
  - **`latest_interaction_at`** = latest cleaned message timestamp across
    non-collapsed threads (falling back to `created_at`), via
    `ThreadSignals.latest_message_at`.
  - **Near-exact message dedup** = deterministic normalization equality
    (lowercase + collapsed whitespace + punctuation/symbol strip); never fuzzy or
    semantic, so negation variants stay distinct. `llm_temperature` is now wired
    through `LlmClient.complete` and bounded `≤ 0.2`. Offline extractor billing/
    feature rules were tightened to phrases (degraded fallback; LLM is primary).
  - **Short-text language detection** remains a documented heuristic limitation
    (single function words may be ambiguous or `UNKNOWN`; `UNKNOWN` is not
    quarantined). 50 Dataset 7 customers report `limited_data` with 0 threads in
    the oracle — an oracle inconsistency; Node 3 correctly emits `no_data`.
- **Phase 5 (Node 4 — Synthesis / Ranked Account List) — complete.** Tasks 5.1–5.12 done:
  `node4/` (`quantitative`, `qualitative`, `scoring`, `rules`, `confidence`, `reasons`,
  `evidence`, `ranking`, `explain`, `node`) and versioned `config/node4/v1.json`
  (`Node4Config` gained `normalization_version` + `top_drivers_max`, and a
  `positive_feedback == 0.00` validator). Implements the §4 contract: two-indexing-system
  alignment (full `customer_ids` vs scored subset, D-6 partial-alignment demotion),
  D-1 identity-clamp normalization, D-2 top drivers, hierarchy×strength qualitative
  score + recurrence bonus, strongest-signal 4-key tie-break, combined score + strong
  agreement bonus, significant-flag predicate, the four critical rules (evaluated before
  level; combined score alone can never be Critical), status-driven insufficient-data
  split (D-8), confidence (`QUANT_CONFIDENCE_BY_STATUS`, D-6 per-customer 0 override),
  deterministic structured reasons + conflict recording, D-3 composite `signal_version`,
  7-key total sort + sequential ranks, D-4 reference-date `ranked_at` (never wall-clock),
  and D-7 duplicate first-occurrence retention. D-5: no LLM in v1 (`explanation` always
  `None`). CLI `churn-survival node4 [--node2 <json>] [--node3 <json>] [--config <v>] [--output <json>]`
  (either upstream optional → §4.14 warnings); `pipeline/main.py` dispatches `node4`;
  `tests/test_pipeline.py` "not implemented" moved to `node5`. `tests/` is now a proper
  package (`__init__.py`) so same-named modules in `tests/node3` and `tests/node4`
  collect without an import mismatch.
- **Node 4 first adversarial QA remediation — done (pending a second QA pass).**
  Confirmed findings fixed: **F-1** `combined_score` is no longer rounded before
  threshold comparison (raw 0.3998→Low, 0.6998→Medium; only `[0,1]` clamp remains);
  **F-2** `positive_feedback` is contextual only — excluded from strongest-risk-signal
  selection, recurrence bonus, `top_flags`, agreement bonus, and critical rules;
  **F-3** `Node4Output` now records the run-level `reference_date` (alongside D-4
  `meta.ranked_at`); **F-4** qualitative score no longer rounded (e.g. `0.45×0.33 =
  0.1485` preserved); **F-6** `no_data` + non-empty `risk_flags` is treated as
  inconsistent upstream input (architecture §3.8.6: `no_data` = zero threads) — an
  `INCONSISTENT_SUPPORT_STATUS` error is recorded and the authoritative status wins,
  so a provably spurious flag cannot drive Critical. **F-5** required no code change
  (under-specified fields are deterministic and schema-compatible). **763 tests
  passing** (186 Node 4); 1 live-LLM skipped; `node4/` coverage 95–100%; ruff +
  `mypy schemas` clean. Dataset 7 CLI smoke distribution unchanged
   (`critical=245 high=0 medium=11 low=4074 insufficient=350`; 3468 customers now carry
   more precise `combined_score`, 0 level changes). A second independent adversarial QA
   pass (27/27 contract-derived checks) then passed; **Node 4 is verified and frozen**
   (see Freeze point).
- Keep this status section accurate; update it as phases complete.

## Freeze point (2026-08-20; Node 4 frozen 2026-09-16)

- **Nodes 1 & 2: verified, frozen** — no changes except regression fixes.
- **Dataset 7: master E2E corpus** — golden hashes pinned; keep stable.
- **Node 3: verified, frozen** — complete + QA-remediated (ROADMAP Phase 4). Do
  not modify Node 3 unless implementation of a later node exposes an actual
  contract defect or integration bug; add regression tests for any such fix.
  575 tests passing, 1 live-LLM skipped; `node3/` coverage 95%; ruff +
  `mypy schemas node3` clean; dataset7 E2E and golden harness green.
- **Node 4: verified, frozen (2026-09-16)** — complete after first adversarial QA
  remediation (F-1…F-6) and a second independent adversarial QA pass (27/27
  contract-derived checks green). 763 tests passing, 1 live-LLM skipped; `node4/`
  coverage 95–100%; ruff + `mypy schemas` clean; Dataset 7 node1→node2→node3→node4
  E2E and CLI smoke green. Decisions D-1…D-9 honored. Do not modify Node 4 unless
  implementation of a later node (Node 5) exposes an actual contract defect or
  integration bug; add regression tests for any such fix. Node 4 must not use an
  LLM, wall-clock time, or infer missing data from low scores.

**Start note for next session:**
1. Node 4 is **verified and frozen**; next is **ROADMAP Phase 6 — Node 5
   (Client-Facing Risk Report)**.
2. Node 5 consumes `Node4Output`; the node4 output schema (incl. run-level
   `reference_date`), `primary_reasons` vocabulary, and evidence refs are stable.
3. Regression QA order if anything changes: Node-4-only → Node 1+2+3 → combined 1→2→3→4.

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

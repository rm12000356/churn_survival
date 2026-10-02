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
  `BB7ADC38…8D8FBF979`, threads `A4A274C7…EB4DE3`, truth `718DD8B3…D0F27F33`
  (byte-identical across runs and platforms; re-pinned 2026-10-01 after the
  truth file's Pearson correlation was made platform-exact). **Documented deviations from the plan (see
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
  `tests/fixtures/dataset7/dataset7_customers_modern.csv` (canonical headers → `clean_csv`) and
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
- **Node 3 multi-source ingestion — done (X.com + Gmail, mock-first).** Node 3 now
  consumes one or more external sources in addition to `support_data`. New
  `schemas/external.py` (`ExternalMessage`), `node3/sources/`
  (`ExternalSource` base, `MockXSource`/`XSource`, `MockGmailSource`/`GmailSource`,
  deterministic `IdentityResolver`, `normalize_threads`, registry), versioned
  `config/node3/sources_v1.json` + `config/identity_mapping/v1.json`, committed
  `mock_sources/x|gmail/*` fixtures (Customers A–G), and additive optional
  `source` provenance on `SupportThread`/`ThreadSignals`/`Evidence`. External
  threads normalize into the existing `SupportThread` contract, so preprocessing,
  extraction, and aggregation are source-agnostic and use the existing `FlagType`
  vocabulary. Identity mapping is explicit/exact (no fuzzy, no LLM); unmapped
  messages are dropped with structured warnings; IDs are namespaced
  (`x:…`/`gmail:…`); the existing §3.3 dedup collapses cross-source duplicates
  without inflating recurrence. CLI: `churn-survival node3 [<threads.json>]
  --sources <x,gmail|mock> [--source-mode mock|live] …`; mock mode needs no
  credentials. Live X/Gmail adapters validate credentials and defer the concrete
  HTTP/OAuth transport (documented dependency decision, no new deps). The
  extraction prompt marks message text as untrusted data (prompt-injection
  hardening). Node 1/2/4/5 unchanged; 956 tests passing, 1 live-LLM skipped;
  ruff + `mypy schemas node3` clean; dataset7 node4/node5 E2E and the Node 3
  golden proxy (κ(flag_type)=0.755, κ(strength)=0.954) unchanged. See
  `docs/node3_multi_source_addendum.md`. **Reported (not fixed): Node 5's Node 3
  evidence description is hardcoded as "reported in a support interaction" and
  does not expose the external source as a first-class field** — a presentation
  gap, not a contract incompatibility.
- **Node 3 multi-source adversarial-QA remediation — done (F-1…F-12; F-13 deferred).**
  An independent adversarial audit of multi-source ingestion returned *PASS WITH
  FINDINGS* (2 HIGH, 5 MEDIUM, 3 LOW, 1 INFO; no CRITICAL). All non-deferred
  findings fixed with regression coverage (addendum §17):
  - **F-1/F-2 (HIGH, prompt boundary):** `llm_extractor.build_thread_prompt` now
    wraps the subject in `<untrusted_subject>` and HTML-escapes the subject,
    message text and the `message_id` attribute, so arbitrary input cannot
    reproduce a structural delimiter; the LLM's referenced `message_id` resolves
    against real and escaped ids and **quarantines on unknown/ambiguous** (fail
    closed). System/task text no longer contains the fence tokens.
  - **F-3 (MEDIUM, determinism):** new `node3/clock.run_timestamp`; thread and
    customer `processed_at` default to `reference_date` midnight UTC (Node 4/5
    rule) instead of wall-clock. CLI re-runs are byte-identical (verified on
    dataset 7; SHA-256 `0941C5C4…C00EF2` both runs).
  - **F-4 (MEDIUM):** malformed source records convert Pydantic `ValidationError`
    → `SourceDataError` via `build_external_message`; `ingest_external_sources`
    records `SOURCE_DATA_INVALID` and other sources continue.
  - **F-5 (MEDIUM):** `build_sources_safe` isolates per-source construction
    failures (`SOURCE_INIT_FAILED`); a bad X no longer blocks Gmail; no silent
    mock fallback; all-fail is visible.
  - **F-6/F-12 (MEDIUM/LOW, selection):** `_select_sources` now gates on
    `enabled` (explicitly requesting a disabled source is a config error), takes
    the env `default_mode`, and rejects `--sources mock` + `--source-mode live`.
    Precedence documented (addendum §10): enabled → `--source-mode` → per-source
    `mode` → `NODE3_SOURCE_MODE`.
  - **F-7 (MEDIUM):** `ExternalMessage.timestamp` must be timezone-aware; rejected
    if naive, canonicalized to UTC otherwise (no naive/aware crash).
  - **F-8 (DATA-INTEGRITY):** `node3/sources/collision.py` detects external
    thread/message ids colliding with support ids (or duplicate external thread
    ids) and drops the external with `ID_COLLISION`; id format unchanged, so
    Node 4/5 evidence stays valid.
  - **F-9 (LOW):** identity errors record `sha256:<12>` instead of the raw
    external identity.
  - **F-10 (LOW):** `redact_secrets` redacts any non-empty explicitly supplied
    secret regardless of length, deterministic order.
  - **F-11 (LOW):** per-source identity case rule documented + implemented
    (gmail casefold, x exact); ambiguous normalized mapping keys fail closed
    (`IDENTITY_MAPPING_AMBIGUOUS`).
  - **F-13 (INFO):** Node 5 external-source wording remains deferred (addendum §14).
  Node 4/Node 5 not modified. 1035 tests passing, 1 live-LLM skipped; `node3/`
  coverage ~96%; ruff + `mypy schemas node3` clean; golden proxy unchanged; both
  dataset-7 E2E chains green.
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
- **Phase 6 (Node 5 — Client-Facing Report) — complete.** Tasks 6.1–6.12 done in three
  milestones. `node5/` (`validation/node4_validator`, `report/` deterministic sections +
  transformer + evidence + recommendations + reason_text + explanation_validator +
  consistency, `llm/` explainer + schemas, `rendering/` json + html, `node.py`), versioned
  `config/node5/v1.json` + `vdataset7.json`, and `config/action_rules/v1.json` +
  `load_action_rules`. Node 5 is a **presentation layer**: it copies Node 4 risk
  level/rank/score/confidence verbatim, never re-sorts, never recalculates, and treats
  `Node3Output` strictly as an evidence *lookup* source (D-U1). Key decisions: deterministic
  `generated_at = reference_date` midnight UTC (D-U3); `CustomerReport.rank: int | None` for
  insufficient accounts, which keep the first-class `insufficient_data` report level and stay
  separate (D-U11/D-INSUF); `evidence_mode` (disabled/summary_only/short_quote/full_evidence)
  with `include_evidence=False` always winning (D-U6); run-level provenance resolved by
  unique-value collection with a structured `MIXED_PROVENANCE` error on disagreement (D-U2);
  `max_accounts_in_summary` is a deterministic **prefix** cap on `priority_accounts` that
  never touches `risk_distribution` (D-ORDER); recommendations are deterministic
  `ACTION_RULES` §5.19-priority (D-REC). The LLM is optional explanation polish only, returns
  strict `{headline, summary, reason_explanations}` (extra fields rejected), and is validated
  by deterministic Python (numbers/dates/timestamps/customer-facts/evidence IDs/risk-level
  mismatch/cancellation claims/confidence-as-probability/contradictions/unsupported
  recommendations) with mandatory template fallback (D-LLM/D-VAL). Node 3 and Node 4 were
  **not modified**. 868 tests passing, 1 live-LLM skipped; `node5/` coverage 93%; ruff +
  `mypy schemas` clean; dataset7 node1→node4→node5 E2E green (distribution preserved, order
  preserved, 350 insufficient separated, all 40 trap customers keep their Node 4 level).
  **Discovered (not a Node 5 bug):** the dataset-7 `node5_trap_oracle` expects traps 001
  (usage_drop, medium) and 002 (billing_complaint, high) one band higher than the real Node 4
  output produces (20/40 customers) — an oracle/Node 4 discrepancy Node 5 must surface, not
  fix (documented in `tests/node5/test_e2e.py`).
- **Node 5 adversarial-QA remediation — done.** An independent audit found 9 issues; all fixed
  with regression coverage (no Node 4 changes, no validation weakening):
  - **F-1 (CRITICAL, evidence):** Node 3 evidence is now keyed by `(customer_id, message_id)`
    and thread ownership is verified, so a foreign reference can never be published.
    Mismatches produce structured `EVIDENCE_CUSTOMER_MISMATCH` / `EVIDENCE_THREAD_MISMATCH`
    errors and omit the evidence (no text/timestamp/thread/flag leakage). Tests cover 8 cases
    incl. duplicate IDs across customers and ranked+insufficient accounts.
  - **F-2 (HIGH, explanation validation):** the validator is rebuilt around an explicit
    allowed-facts model. It rejects unsupported recommendations (action vocabulary derived
    from ACTION_RULES), risk factors (flag→concept mapping), material customer facts, numeric
    claims (percentages always rejected; spelled-out numbers checked), dates (date-like
    expressions only — **F-6**: "may" as a verb is no longer a date), and altered risk levels.
    Removed the capitalization/sentence-position heuristic.
  - **F-3 (MEDIUM, provenance):** required provenance must be non-empty and mixed upstream
    versions block publication (`DoNotPublishError`); `action_rules_version` is required when
    recommendations are enabled. No version is fabricated.
  - **F-4 (LOW):** malformed `top_flags` entries raise structured `INVALID_TOP_FLAG` errors
    (no silent drops, no `KeyError`).
  - **F-5 (LOW):** duplicate insufficient-data IDs and cross-list membership are rejected.
  - **F-6 (LOW):** see F-2 date rules.
  - **F-7 (LOW):** the HTML renderer now emits account-level Quantitative signals and Support
    signals (validated fields only); PDF remains intentionally deferred (no approved
    dependency) and is documented in `node5/rendering/__init__.py`.
  - **F-8 (INFO):** display names are bounded (≤120 chars), control chars stripped, whitespace
    collapsed, falling back to `customer_id` when empty.
  - **F-9 (INFO):** LLM `reason_explanations` are validated (rejecting unsupported claims) but
    not surfaced — the locked §5.9 `CustomerReport` schema has no field for them (documented in
    `explanation_validator.py`); the dead `Node3EvidenceIndex.signals` was removed.
  899 tests passing, 1 live-LLM skipped; `node5/` coverage 94%; ruff + `mypy schemas` clean;
  Dataset 7 E2E green.
- **Phase 7 (Orchestration) — complete.** Implemented as a deterministic, plain-Python
  state machine, **not LangGraph** (architecture §6.5 permits "LangGraph **or
  equivalent**"; no graph framework is imported). `orchestration/`: `state.py`
  (`PipelineState`/`PipelineResult`, fully JSON round-trippable), `routing.py` (LLM-free
  routing reusing `router.route`), `mapping.py` (`MappingGate` protocol +
  `persist_confirmed_mapping`; the human confirmation gate is the only path to
  persistence and is never auto-confirmed), `graph.py` (`run_pipeline` /
  `resume_pipeline`), and `node.py` + a `pipeline/main.py` `run` subcommand. Stop
  conditions: `STOPPED_NEEDS_MAPPING` (unmatched shape, no confirmed mapping),
  `STOPPED_VALIDATION` (Node 1 batch `FAILED`, §1.2 short-circuit), `FAILED`
  (structured `NODE_EXCEPTION`; every completed node output retained), `COMPLETED`.
  Support inputs are optional but **Node 3 always runs** for the canonical universe
  (a deterministic no-data baseline) so Node 5 has a non-empty
  `node3_signal_version` and can publish. `persist_artifact` defaults to **False**
  (demo-friendly); model versions are content-addressed, so identical inputs
  overwrite the same `models/<model_version>/`. Resume re-enters at **routing** and
  re-runs Node 1–5 (no hot mid-pipeline resume); a cross-request web resume confirms
  the mapping through the gate, then calls `resume_pipeline`/`run_pipeline` again.
  Decisions D-O1…D-O6 are locked in `docs/phase7_orchestration_plan.md`. 1076 tests
  passing, 1 live-LLM skipped; `orchestration/` coverage 97–100%; ruff +
  `mypy schemas` clean; dataset 7 `churn-survival run` E2E green (Node 5
  distribution + order preserved); re-runs deterministic.
- **Phase 8 (Persistence & API) — complete (2026-09-27).** Decisions D-P1…D-P13 are
  locked in `docs/phase8_persistence_api_plan.md`.
  - **Routing-inclusive, content-addressed `run_id`** (`orchestration/identity.py`,
    decision-free — no node imports): the resolved routing decision + every config
    version + raw/support digests + `reference_date`. Because the mapping registry
    is input configuration, confirming a mapping yields a **new** run id; the
    stopped run survives as audit (never a stale ID pointing at a completed result).
    `PipelineState` gained `run_id`/`raw_digest`/`support_digest`/`routing_identity`.
  - **Run store + load-bearing SQLite index** (`orchestration/persistence.py`,
    `orchestration/index.py`): `runs/<run_id>/{state.json,summary.json,node1..4.json
    (present only),node5.json,report.html}` + `runs/index.sqlite`. `RunSummary`
    (`schemas/run.py`) is both the index row and the API shape. Self-heals from
    disk; legacy rows surface `routing_identity_source=unknown_pre_migration`
    (nullable routing fields, never an empty string).
  - **Hybrid manual GC** (`orchestration/gc.py`, `churn-survival gc`): count-based
    for model artifacts + runs, TTL-based for `STOPPED_*`/`INTERRUPTED` run dirs and
    unconfirmed `config/mappings/drafts/*.json`; confirmed mappings never pruned;
    `--recover` marks stale `RUNNING` rows `INTERRUPTED`. `GC_ON_STARTUP=false`.
  - **FastAPI serving** (`api/`): read endpoints (`GET /health`, `/runs`,
    `/runs/{id}`, `/runs/{id}/report(.html)`, `/runs/{id}/ranked-accounts`,
    `/runs/{id}/node1..4`, `/models`, `/models/{version}`) **never recompute**
    (enforced by a monkeypatch contract test). `POST /runs` triggers exactly one
    async `run_pipeline` on a single-worker executor (`202` + client polls); the
    status matrix (D-P11) caches `COMPLETED`/`STOPPED_*`, resubmits
    `FAILED`/`INTERRUPTED`, and rejects `force` on `STOPPED_*` (`400`) / while
    `RUNNING` (`409`). `supersedes_run_id` links audit lineage. Auth: reads open
    when `API_KEY` unset; `API_ENABLE_WRITES=false` by default (all POSTs `403`);
    enabling writes requires a key at startup. `POST /mappings/{draft,confirm}` —
    confirm is always write-gated + authenticated and persists **only** through
    `orchestration/mapping.persist_confirmed_mapping`, recording the actor.
  - CLI: `churn-survival run … --persist-run [--run-dir <p>]`,
    `churn-survival gc …`, `churn-survival-api` (uvicorn). `fastapi`/`uvicorn`/
    `httpx` added to the `dev` extra.
  - **1130 tests passing, 1 live-LLM skipped; ruff + `mypy schemas` clean;** dataset
    7 persisted and byte-identical across two full runs; E2E covers stop → confirm →
    retry (`X1 ≠ X0`, `X0.node1` absent, `X1` completes, `superseded_by = X1`).
- **Phase 9 (Production Hardening) — complete (2026-09-27).** Decisions D-H1…D-H9
  locked in `docs/phase9_production_hardening_plan.md`.
  - **Observability (D-H1/D-H2/D-H3/D-H4):** structlog wired at the orchestration,
    API, and node-CLI boundary only (no frozen node logic changed; **no
    OpenTelemetry** — §8.8 says keep it simple unless already in use).
    `logging_setup.py` gains `LOG_FORMAT` (json/console), **stderr** output via a
    lazy dynamic-stderr proxy (so pytest captures never go stale), run/request
    context helpers, and `emit_node_completion`. `orchestration/graph.py` emits
    `run_started`, per-node `stage_finished` (node + config version + returned
    version fields + counts), terminal `run_completed`/`run_stopped`/`run_failed`;
    `api/` logs `http_request` + run lifecycle. No secrets or support text is
    logged; timestamps/durations are operational and never enter outputs.
  - **Golden sets (D-H5/D-H6):** `scripts/eval_node3_golden.py` exposes pure
    `evaluate_golden(...) -> GoldenResult`; `tests/golden/test_node3_golden.py`
    (`@pytest.mark.golden`) enforces the §3.10 bars on the offline extractor
    (κ flag ≥ 0.70, κ strength ≥ 0.65, exact-match ≥ 0.75). `.github/workflows/ci.yml`
    runs `ruff` → `mypy schemas` → **generate + validate dataset 7** (the corpus is
    gitignored; CI regenerates it deterministically) → `pytest`.
  - **Reproducibility audit (D-H7/D-H9):** `scripts/audit_reproducibility.py` (+
    `churn-survival audit --run-id <id>|--all`) re-runs a persisted run and
    byte-diffs `node1..node5.json`/`report.html`. A **routing pre-flight** compares
    the recorded `routing_identity` with a fresh fingerprint+route pass and
    short-circuits to `MAPPING_CHANGED` (skip) before the costly re-run; a
    support-digest gate short-circuits to `MISSING_SUPPORT_INPUTS` (skip; support
    inputs are re-supplied, never stored). A genuine non-determinism regression
    surfaces as `FAIL` with the differing paths. An in-place mapping mutation that
    keeps the same adapter version is documented as byte-diff `FAIL`.
  - **1141 tests passing, 1 live-LLM skipped; coverage 95%; ruff + `mypy schemas`
    clean**; `node1..node5` decision logic unchanged.
- **Horizon frontend (additive) — done (2026-09-27).** A dependency-free static
  UI (`frontend/`) that drives the Phase 8 API: upload → orchestrated run →
  mapping confirmation → ranked report → run history. Vanilla ES modules + CSS
  served same-origin by FastAPI (`StaticFiles` mounted at `/` **after** the API
  routers, so explicit paths keep precedence; mounted only when `FRONTEND_DIR`
  exists — the API-only deployment is unchanged). **The UI never computes a risk
  level, score, rank, or confidence** — every value renders verbatim from an API
  response; no `run_id`/fingerprint is constructed client-side. Additive backend
  changes (no frozen decision logic touched):
  - **`GET /raw-files` + `POST /uploads`** (`api/routes/uploads.py`,
    `api/schemas.py`): list datasets under `RAW_DATA_DIR` and accept a multipart
    upload (write-gated + authenticated; basename-only filename sanitation,
    extension allow-list, 200 MiB cap, empty-file rejection). `POST /runs`
    remains the single computing trigger; `python-multipart` added to the `api`
    and `dev` extras.
  - **Per-account explanation provenance** (`schemas/node5.py`,
    `node5/report/transformer.py`, `node5/node.py`, `node5/rendering/html.py`):
    `CustomerReport.explanation_source` (`"llm"`/`"template"`) and
    `Node5ProcessingReport.explanation_source_summary` counts. Presentation-only
    (not a decision field); the served HTML report tags each account and the
    frontend renders an "LLM-drafted vs template" badge. This closes the
    previously reported Node 5 external-wording/provenance presentation gap.
  - **LLM wired into API runs** (`api/service.llm_client_or_none`, shared with
    `api/routes/mappings.py`): `execute_run` now passes the optional LLM client
    to `run_pipeline`, so Node 5 explanation polish (and Node 3 extraction) can
    run for API-triggered runs; with `LLM_PROVIDER=none` every run stays
    deterministically template-only. The LLM still has **zero** decision
    authority (validated + template fallback).
  - **Frontend screens:** Upload/Trigger (three `POST /runs` outcomes handled),
    Run Status (2s polling; terminal branching incl. `STOPPED_VALIDATION`
    reasons and `FAILED` `error_code`+`stage`, never a stack trace; `INTERRUPTED`
    resubmit), Mapping Confirmation (editable audit table surfacing the audited
    transforms; re-triggers a **new** run with `supersedes_run_id`), Ranked Report
    (separate insufficient-data section; detail drawer with provenance tag), Run
    History (status filter + `superseded_by` lineage), Models. Design tokens,
    light/dark, tabular figures, single-column responsive tables (horizontal
    scroll, not wrap).
  - **Verified:** `pytest` **1152 passing**, 1 live-LLM skipped; `tests/api/
    test_uploads.py` + `test_frontend_static.py` + Node 5 provenance tests added;
    `ruff check .` + `mypy schemas` clean; manual round-trips: dataset7
    upload→poll→report COMPLETED (provenance `template`), and a stop-needing-mapping
    fixture → confirm → re-trigger produced a **new** `run_id` with
    `superseded_by` chaining (`X1 ≠ X0`, `X0.superseded_by == X1`).
  - **Horizon UX bugfix + JSON support threads (2026-09-27).** Two defects found
    in live use, both fixed:
    1. **Run-id bug (blocked every run view).** `router.js` parses `#/runs/<id>`
       into `segments=[\"runs\",\"<id>\"]` with an **empty** `params`, but
       `runStatus.js`/`report.js`/`mapping.js` read `ctx.params.id` → `undefined`
       → `GET /runs/undefined` → server `404 unknown run 'undefined'`. Fixed
       centrally: `app.js` now sets `ctx.params.id = parsed.segments[1]`; views
       also render a graceful "No run selected" / "Run not found" state.
    2. **Picker offered unrunnable files.** `GET /raw-files` listed everything in
       `RAW_DATA_DIR` (incl. the Node 3 `*_threads_*.json`), so selecting it and
       triggering `POST /runs` raised `ValueError("unsupported raw-data extension
       '.json'")` → `422 could not prepare run`. Fixed by **classifying** each file
       with a new `kind` field: `"dataset"` (Node 1 CSV/Excel) vs `"support"`
       (Node 3 support-threads JSON); uninrunnable extensions are not listed, and
       `POST /uploads` rejects `.tsv/.parquet`. The upload view now separates a
       **required customer dataset** picker from an **optional support-threads**
       picker and only enables **Run** when a dataset is chosen (plus a
       double-submit guard).
    - **JSON support threads (Option 1):** a support JSON is a Node 3 input, not
      a customer dataset. New read-only `GET /raw-files/{name}` (confined to
      `RAW_DATA_DIR`) lets the UI load the array and send it as
      `RunTriggerRequest.support_data`; Node 3 validates it as `SupportThread`.
      No Node 1/Node 3 contract change — Node 1 stays frozen.
    - **Readable errors:** the frontend banner now renders FastAPI `detail`
      (string or validation array) instead of raw JSON.
    - Verified: `pytest` **1155 + new tests passing**, 1 live-LLM skipped;
      `tests/api/test_uploads.py` (kind classification, confined read,
      `support_data` reaches Node 3, JSON-as-`raw_path` → 422) and
      `test_frontend_static.py` (run-id wiring, support picker) added;
      `ruff check .` + `mypy schemas` clean.
    - **Content-type response parsing (2026-09-27).** The `fetch` wrapper in
      `frontend/static/api.js` no longer blind-JSON-parses every response. It now
      parses by `content-type`: JSON endpoints yield objects, while `text/plain`
      (`GET /raw-files/{name}` support threads) and `text/html`
      (`GET /runs/{id}/report.html`) yield strings. This fixes support-thread
      loading, which previously failed with a client-side "is not valid JSON"
      because the wrapper had already parsed the body into an object (the caller's
      `JSON.parse` then received `[object Object]`). Added `getReportHtml` +
      `reportHtmlUrl`; the report screen now links to the server-rendered static
      report in a new tab. Frontend-only; no Node/API contract change.
      `pytest` 1165 passing, ruff + `mypy schemas` clean.
- **Per-section inputs, user mapping files, route-once performance (2026-09-30).**
  - **Frontend:** the Upload screen has one section (with its own file upload) per
    input — 1 customer dataset (Node 1), 2 support threads (Node 3), 3 column
    mapping (user's own mapping JSON: a draft report or a confirmed `map_*.json`),
    4 run settings. The Mapping screen (run `STOPPED_NEEDS_MAPPING`) offers **Map it
    myself / Ask the LLM / Upload my mapping file**, all feeding one editor with a
    row per dataset column (the manual draft has zero rows, so the old table had
    nothing to edit), plus its own support-threads section so the re-triggered run
    keeps Node 3 input, and a retry-safe confirm (never re-confirms). Shared
    components: `frontend/static/components/{filePicker,mappingFile,mappingEditor}.js`.
  - **`POST /mappings/confirm`:** binds the report's `source_fingerprint` to the
    dataset being confirmed (so an uploaded file routes that dataset), rejects
    source columns the dataset lacks (422), and returns 409 when the shape already
    has a confirmed mapping — `confirm_and_persist` now refuses duplicate
    `headers_hash` and never overwrites a config file (review H4).
  - **Performance (output byte-identical on all 7 onboarded datasets):** the mapping
    adapter resolves each column's transform once (`compile_transformation`) and
    reads rows from `frame.values` instead of per-cell Series lookups; the
    multicollinearity warning builds each column once and skips when no core keys
    are approved. Node 1 on Cell2Cell 71k rows: 48 s → 10 s. Routing happens once:
    fingerprints are cached per file version (`orchestration.routing.fingerprint_file`)
    and `run_node1(decision=...)` reuses the orchestrator's adapter (re-routes only
    if it no longer matches) — raw loads per API run 4 → 2.
  - 1184 passed, 1 skipped; the only failure is the pre-existing
    `test_router_german_csv_unmapped` caused by the committed
    `config/mappings/map_20260927T181905Z.json` (REVIEW.md H3). ruff + `mypy schemas` clean.
- **LLM latency blocker (REVIEW.md §5) — resolved (2026-09-30).** Node 5 LLM polish
  is opt-in (`Node5Config.llm_enabled`, default false) and bounded
  (`llm_max_accounts`, `llm_max_consecutive_failures` circuit breaker, shipped
  `llm_max_retries: 0`, collapsed warnings). The explanation validator accepts
  digit tokens for allowed integers ("90-day"), masks the account's own
  id/display name, and requires a supporting flag for pricing/switching/
  dissatisfaction language. Node 3 LLM extraction is concurrent and bounded
  (`Node3Config.llm_max_concurrency`, default 8; order-preserving, output
  identical). `RUN_MAX_WORKERS` default 2. Decision logic unchanged. Two stale
  Node 2 tests updated to the already-applied Node 2 review fixes.
  **Follow-up speed-ups:** Node 5 explanations run in ordered batches on a thread
  pool (`Node5Config.llm_max_concurrency`, default 4; breaker/cap checked between
  batches; output byte-identical to sequential). `LlmClient` shares one pooled,
  thread-safe `httpx.Client` (keep-alive) and retries 429/5xx/transport errors
  (3 attempts, `Retry-After` or exponential backoff). `NODE3_LLM_MAX_CONCURRENCY`
  / `NODE5_LLM_MAX_CONCURRENCY` settings override the config values in
  `run_pipeline` and are deliberately excluded from `run_id`.
  1249 passed, 1 skipped; ruff + `mypy schemas` clean.
- **Horizon UI refresh (2026-09-30, frontend-only).** Same tokens/fonts/no-card
  language, extended: report "horizon band" (`ui.horizonBand`, proportional to the
  API's `risk_distribution` counts), risk rail + score meter on ranked rows,
  keyboard-accessible rows (`ui.actionRow`), modal drawer (scrim, Esc, focus
  return), connected pipeline stepper (`settled` when terminal), run-status badges
  separate from risk badges (`ui.statusBadge`), numbered upload steps, sentence-case
  labels, focus rings, reduced-motion, `prefers-color-scheme` default, mobile grid
  overflow fix. History "Completed" filter now actually filters. Still no client-side
  risk/score/rank math. Follow-up: report lists are searchable, level-filterable
  (chips + clickable legend), paged 100 at a time, and export the visible selection
  as CSV (API order and values, never re-sorted); mapping shows a live required-field
  checklist + inline problems and enables Confirm only when valid; models load in
  parallel; static frontend is served `Cache-Control: no-cache` (`api/app.py`) so
  browsers never run a stale view. `tests/api` 71 passing; 23-check Playwright
  click-through (Edge) green.
- **Optional support → quantitative-only synthesis (2026-09-30, owner decision; architecture §4.14a).**
  Node 4 config **v2** (`config/node4/v2.json`, now the default in `run_pipeline`,
  `churn-survival run`/`node4` and `POST /runs`) adds `quantitative_only_without_support`.
  When a run supplies no support threads (`run_node4(..., support_supplied=False)`, passed by
  the orchestrator; inferred for direct callers), the combined score and confidence come from
  the survival model alone and no per-account `missing_support_data` / no-data conflict reason
  is emitted (one run-level warning instead). Before: every account got confidence 0.385 and
  the score was capped at 0.60 (High unreachable). v1 is unchanged (bit-identical); with
  support supplied, v1 and v2 decisions are identical (verified on dataset 7). Tests:
  `tests/node4/test_optional_support.py`. 1257 passed, 1 skipped (`LLM_PROVIDER=none`).
- **REVIEW.md §6 remediation — done (2026-10-01).** Every §6 finding addressed (status table
  in `REVIEW.md` §7). Highlights:
  - **Run identity (N-H3, owner decision "bump versions"):** `orchestration.identity.CODE_SEMANTICS_VERSION`
    (`"2026.10.1"`) is recorded as `config_versions["semantics"]` by `run_pipeline` and the API
    trigger, so **every run_id changed once**. **Rule:** bump it whenever a code change can alter
    any node's output for the same inputs + config versions. Provenance strings bumped too:
    Node 2 `modeling_version` 1.1.0 + `FIT_ALGORITHM_VERSION="fit-2"` in `model_version` (N-H1),
    Node 3 `aggregation_version` agg_v1.1 + offline extractor tag `offline_v2`, Node 5
    `report_version` 1.1, Node 1 `validation_version` 1.0.2 (dataset7 1.1.1), built-in adapters
    1.1.0. The reproducibility audit skips `SEMANTICS_CHANGED` and `LLM_ENABLED` runs.
  - **Node 2:** C-index ranks the delivered `1 − S(t_ref)` for stratified fits and is labelled
    `c_index_kind="apparent"` (N-H2); survival CI bounds are `None` (never NaN) and Cox bands carry
    `ci_approximate=true` (N-M14); artifacts publish atomically via a staging dir (N-H8).
  - **Node 3:** run-level warning + ordered consecutive-failure circuit breaker
    (`llm_max_consecutive_failures`, default 5) for LLM outages (N-H5). **Bug found and fixed:**
    customer `meta.model_version` is now the run's extraction model, so an LLM run where some
    customers had no threads no longer fails Node 5's MIXED_PROVENANCE gate.
  - **Node 5:** breaker discards the rest of a batch (output identical at any concurrency, N-H4);
    provider errors vs validation rejections, 401/403 trips at once (N-M4); reason codes only,
    never LLM text, in warnings (N-M5); warns on client/`llm_enabled` mismatch (N-M1).
  - **API:** pure-ASGI `BodyGuardMiddleware` rejects unauthenticated/writes-off/oversized
    (incl. chunked) bodies before reading them; `RUN_MAX_QUEUED`, `RUN_TRIGGERS_PER_MINUTE`,
    `UPLOADS_PER_MINUTE` back-pressure (N-H6); submit failure → FAILED `ENQUEUE_FAILED` + 503
    (N-M3); failures store a sanitised message, `PERSIST_ERROR` vs pipeline (N-M2); `GET /runs`
    `offset` paging (N-M16); uploads publish with `os.link`, never overwrite (N-M15);
    `/raw-files` lists bare names (POST /runs resolves them in `RAW_DATA_DIR`), symlinks confined,
    no mkdir on GET; OpenAPI key scheme + documented POST /runs responses; startup recovery runs
    only for the run-store owner (`.api-owner.lock` + heartbeat).
  - **Mapping confirm (N-H7):** lock (thread + lockfile) around check-and-write, hard-link
    publish, `_N` suffix on same-second names. **Fingerprint cache (N-M13):** key includes a
    head+tail 64 KiB digest. **Persistence/GC (N-M6):** index reconciles when disk has more runs,
    skipped dirs are logged, GC never prunes runs with unreadable metadata.
  - **Frontend:** per-route render context/host (N-M7), guarded sessionStorage key (N-M8,
    LOW), assessed total excludes insufficient data (N-M11), poll retry/backoff + honest resubmit
    (N-M9), confirm in-flight guard + lock after save (N-M10), keyed report via sandboxed iframe
    (N-H9), formula-safe CSV with BOM (N-H10), WCAG fixes N-A1…N-A8 + minors (contrast tokens,
    field borders, inert drawer + Tab trap, labels, in-place chips, live regions, real row
    buttons, skip link, rem font sizes). Edge click-through 28/28.
  - Tests: T1 (hermetic `.env` autouse fixture), T2–T9 added. CI: pip-audit allow-list
    `.github/pip-audit-ignore.txt`. **1352 passed, 2 skipped; coverage 94%; ruff + `mypy schemas
    node3` clean; dataset 7 validate 58/58; golden κ gate green.**
- **Reads open by default (2026-10-01).** With `API_KEY` set, every read returned 401
  until a key was pasted in the UI. New setting `API_REQUIRE_KEY_FOR_READS` (default
  `false`): reads are open, and the key gates writes only (route dependencies +
  `BodyGuardMiddleware`, unchanged). Set it to `true` to restore authenticated reads (H1).
- **Per-run LLM selection (2026-10-01).** API runs are LLM-free by default even when
  `LLM_PROVIDER` is set. `POST /runs` takes `llm_node3` (Node 3 extraction) and
  `llm_node5` (Node 5 explanation polish, forces `llm_enabled`); 422 when no LLM is
  configured. `run_pipeline(llm_nodes=...)` routes the client (`None` = legacy: both
  nodes, identity unchanged) and records `config_versions["llm_nodes"]`, so every
  choice is a distinct `run_id`. `GET /health` reports `llm_available`/`llm_model`; the
  Upload screen shows the two toggles (disabled when no LLM is configured).
- **Phase 10 — forward-looking risk, lift levels, churned split, per-customer
  confidence (2026-10-01, owner decision).** Every run used to put all customers in
  Low with one confidence per run: Node 4 used `1 − S(90d)` (churn in the first 90
  days of tenure), compared it to absolute thresholds, and ranked already-churned
  customers. Decisions D-R1…D-R5 are locked in `docs/phase10_risk_scale_confidence_plan.md`; architecture §2.12a,
  §4.3, §4.4a, §4.15a amended. These are additive, versioned amendments to the
  frozen Nodes 2/4:
  - **Node 2:** `forward_survival` (`S(T+t)/S(T)`, Cox `conditional_after`, KM from
    `event_table`, Greenwood log-log CI; `None` for churned and past follow-up),
    `customer_tenure_days`, `customer_event_observed`, `max_follow_up_days`.
    `modeling_version` 1.2.0, so `model_version` changed once.
  - **Node 4 v3** (default in `run_pipeline`, the `run`/`node4` CLIs and
    `POST /runs`):
    - lift scale `risk_norm_v2` (1.5× average = Medium, 3× = High);
    - `churned_accounts` + `n_churned`;
    - per-customer `conf_v2` = model × precision × history, with
      `confidence_factors`;
    - customers past follow-up count as missing, never Low.

    v1/v2 decisions are unchanged.
  - **Node 5 report_version 1.2:** churned section and three-list check. The
    frontend shows the churned list, forward/lift facts and the confidence breakdown.
  - `CODE_SEMANTICS_VERSION` is `2026.10.2`, so every `run_id` changed once.
  - **Results:** dataset 7 without threads gives High 27 / Medium 774 (was all
    Low), and with threads every v2 Critical account stays Critical or is now
    churned. Cell2Cell lists 20,609 churned.
  - **Known:** High accounts with a forward CI ≥ 0.20 get precision 0, so
    confidence 0 in quantitative-only runs (Telco 46, Cell2Cell 9).
  - **Verified:** 1408 passed, 2 skipped; coverage 94%; ruff + `mypy schemas
    node3` clean; dataset 7 validate 58/58; byte-identical re-runs; audit PASS.
- **Per-account model drivers (2026-10-01, plan v3).** REVIEW.md L26 fixed: Node 4
  showed one model-wide `top_drivers` list on every account. Decisions C-1…C-9 are
  locked in `docs/node2_model_contributions_plan.md`; architecture §2.12b and §4.4b
  amended. Explanation only: no score, level, rank or confidence changes.
  - **Node 2:** `customer_contributions` (`β·(x − ref)` against the *model
    reference profile*: training mean or reference category),
    `customer_relative_log_hazard`, `baseline_log_hazard`. They are scored-aligned,
    full precision, CoxPH only, with `LP = baseline + relative` exact.
    `modeling_version` is 1.3.0, so `model_version` changed once.
  - **Node 4 v4** (the new default):
    - `per_customer_drivers` + `drivers_require_reliable`: positive and reliable
      contributions only, sorted, capped;
    - `quantitative.driver_details` + `relative_log_hazard`;
    - per-account `feature_refs` and reason `evidence_ref["drivers"]`.

    v1–v3 are bit-identical even with the new Node 2 fields.
  - **Node 5:** per-account driver wording (`node5/report/driver_text.py`) in
    evidence, the template summary (up to 5 sentences) and HTML. Driver facts are
    registered with the LLM validator.
  - `CODE_SEMANTICS_VERSION` is `2026.10.3`, so every `run_id` changed once.
  - **Results:** dataset 6 goes from 1 driver set to 12, and
    `support_tickets_90d` (CI includes 1) is gone. Dataset 7 has 36 sets. v3 and
    v4 decisions are identical on dataset 6 and on dataset 7 (with and without
    support), and re-runs are byte-identical.
- **A confirmed mapping is enough to run a new format (2026-10-01).** Onboarding a
  real e-commerce workbook (5,630 rows; sheets `Data Dict` + `E Comm`) through the UI
  needed manual work after the LLM mapping. Three gaps were fixed (architecture §1.6
  amendment; `docs/onboarding.md`):
  - **Primary sheet:** `SourceFingerprint.primary_sheet` = the sheet with the most
    rows (ties: earlier sheet). The mapping adapter and the LLM prompt use it, so a
    workbook that opens with a data dictionary is mapped from its data. Legacy
    mappings keep the first-sheet behaviour.
  - **Derived Node 1 config:** confirming without `node1_config_version` writes
    `config/node1/v<mapping_version>.json` (`router.llm_mapper.derive_node1_config`:
    approved keys = the mapping's `core.*` targets, typed from `CoreFeatures`; rest
    from `v1`) and records it on the mapping. It is never overwritten, and runs no
    longer fall back to `v1` core keys the dataset lacks.
    `load_node1_config(..., config_root=)` searches the API/orchestration
    `CONFIG_DIR` first, then the settings dir.
  - **Core type check:** a `core.<key>` mapping whose output type can't match the
    key is demoted to extras with a data-quality flag (LLM proposals) or rejected
    at confirm (human reports) — `adapters.mapping_adapter.transformation_output_kind`
    + `router.llm_mapper.core_type_mismatch`.

  `CODE_SEMANTICS_VERSION` → `2026.10.4`. Verified: the original 2-sheet workbook goes
  stop → LLM draft → confirm (no config) → `COMPLETED` (Node 1 `PARTIAL 5366/264`); the
  7 onboarded datasets keep identical Node 1 counts. Tests:
  `tests/router/test_primary_sheet_and_derived_config.py` (28) and
  `tests/e2e/test_workbook_onboarding.py`.
- Keep this status section accurate; update it as phases complete.

## Freeze point (2026-08-20; Node 3 multi-source frozen 2026-09-16; Node 4 frozen 2026-09-16; Node 5 frozen 2026-09-16; Phase 7 orchestration complete 2026-09-27; Phase 8 persistence & API complete 2026-09-27; Phase 9 production hardening complete 2026-09-27; Horizon frontend complete 2026-09-27)

- **Nodes 1 & 2: verified, frozen** — no changes except regression fixes.
- **Dataset 7: master E2E corpus** — golden hashes pinned; keep stable.
- **Node 3 (multi-source form): verified, frozen (2026-09-16)** — complete
  (ROADMAP Phase 4) plus multi-source ingestion (X.com + Gmail, mock-first) and
  the full adversarial-QA remediation (F-1…F-12; F-13 deferred). Locked
  properties: deterministic `processed_at` from `reference_date`
  (`node3/clock.run_timestamp`); untrusted subject/message fences with
  HTML-escaped payloads + message ids and fail-closed id resolution;
  timezone-aware (UTC) external timestamps; per-source construction/fetch
  isolation (`SOURCE_INIT_FAILED`/`SOURCE_DATA_INVALID`); support/external id
  collision detection (`ID_COLLISION`); hashed identity errors; per-source
  identity case rule; and documented source-selection mode precedence. No
  wall-clock time, no unescaped untrusted content, no silent mock/live switching.
  1035 tests passing (242 in `tests/node3`), 1 live-LLM skipped; `node3/`
  coverage ~96%; ruff + `mypy schemas node3` clean; dataset7 E2E and golden
  proxy (κ 0.755 / 0.954) green; CLI re-runs byte-identical. Do **not** modify
  Node 3 unless a later node exposes an actual contract defect or integration
  bug; add regression tests for any such fix. Node 4/Node 5 remain untouched by
  the Node 3 multi-source work.
- **Node 4: verified, frozen (2026-09-16)** — complete after first adversarial QA
  remediation (F-1…F-6) and a second independent adversarial QA pass (27/27
  contract-derived checks green). 763 tests passing, 1 live-LLM skipped; `node4/`
  coverage 95–100%; ruff + `mypy schemas` clean; Dataset 7 node1→node2→node3→node4
  E2E and CLI smoke green. Decisions D-1…D-9 honored. Do not modify Node 4 unless
  implementation of a later node (Node 5) exposes an actual contract defect or
  integration bug; add regression tests for any such fix. Node 4 must not use an
  LLM, wall-clock time, or infer missing data from low scores.
- **Node 5: verified, frozen (2026-09-16)** — Phase 6 implemented in three milestones
  A/B/C and remediated after an independent adversarial QA pass (F-1…F-9, all fixed with
  regression coverage; F-1 customer-bound evidence, F-2 explicit allowed-facts explanation
  validation, F-3 mandatory provenance). 899 tests passing, 1 live-LLM skipped; `node5/`
  coverage 94%; ruff + `mypy schemas` clean; Dataset 7 node1→node2→node3→node4→node5 E2E
  green. Decisions D-U1…D-U11/D-REC/D-VAL/D-ORDER/D-RENDER honored. Do not modify Node 5
  unless implementation of a later phase (Phase 7 orchestration / Phase 8 persistence & API)
  exposes an actual contract defect or integration bug; add regression tests for any such
  fix. Node 5 must not use an LLM for any decision, re-sort Node 4 accounts, recalculate
  risk, use wall-clock time for `generated_at`, or publish evidence that is not owned by the
  referencing customer.

**Start note for next session:**
1. Nodes 3 (multi-source), 4 and 5 are **verified and frozen**; Phase 7 orchestration,
   Phase 8 persistence & API, and **Phase 9 production hardening** are complete.
   Hardening decisions D-H1…D-H9 are locked in `docs/phase9_production_hardening_plan.md`
   (structured logging to stderr at the boundary; Node 3 golden gate in `pytest`/CI;
   reproducibility audit with `MAPPING_CHANGED`/`MISSING_SUPPORT_INPUTS` skips). When
   starting new work, keep the CI workflow (`.github/workflows/ci.yml`) green.
2. Phase 8: the API is a serving layer — read endpoints never recompute (single-writer-of-
   decisions); `POST /runs` enqueues one async `run_pipeline` and clients poll
   `GET /runs/{id}`. Confirmed mappings persist **only** through
   `orchestration/mapping.persist_confirmed_mapping`; writes require `API_ENABLE_WRITES=true`
   + `API_KEY`. `run_id` is routing-inclusive — do not weaken it (the audit's
   `MAPPING_CHANGED` pre-flight depends on it).
3. Node 5 consumes `Node4Output` (+ optional `Node3Output` as an evidence lookup, optional
   `customer_data`) and emits `Node5Output`; it is a presentation layer only.
4. The implementation authority for Node 5 is `docs/phase6_node5_implementation_plan.md`
   (decisions D-U1…D-U9/D-REC/D-VAL/D-ORDER/D-RENDER are locked there; §25.1 records the QA
   remediation). Node 3 multi-source decisions are locked in
   `docs/node3_multi_source_addendum.md` (F-1…F-12 remediation in §17; F-13 deferred in §14).
   Phase 7 orchestration decisions D-O1…D-O6 are locked in
   `docs/phase7_orchestration_plan.md`; Phase 8 decisions D-P1…D-P13 are locked in
   `docs/phase8_persistence_api_plan.md`.
5. Regression QA order if anything changes: persistence/API-only → orchestration-only →
   Node-5-only → Node-4-only → Node 3 → Node 1+2 → combined 1→2→3→4→5.

## Planned repo layout (ROADMAP Task 0.2 / architecture §8.10)

```
churn_survival/
├── adapters/               # deterministic adapters + base class
├── schemas/                # Pydantic models (canonical, reports, outputs)
├── router/                 # signature detection + routing logic
├── node1/ ... node5/       # one package per node
├── models/                 # saved model artifacts + mapping configs
├── runs/                   # persisted pipeline runs + SQLite index (Phase 8)
├── orchestration/          # plain-Python orchestration (routing + sequencing + persistence)
├── api/                    # FastAPI serving layer (Phase 8)
├── frontend/               # Horizon static UI (served by FastAPI)
├── tests/
├── config/                 # versioned config files (thresholds, vocab, prompts)
├── data/raw|processed/
└── pyproject.toml
```

## Hard rules (never violate)

1. **Determinism** — same inputs + same config versions → bit-identical output. `reference_date` is a declared cut-off, never "today" at runtime.
2. **LLM has zero authority** — over any score, rank, risk level, confidence, or evidence. Optional, explanation-polish only (Node 5) or thread-level extraction (Node 3). Deterministic fallback is mandatory.
3. **The combined score alone can never produce Critical.** Explicit critical rules only (Node 4 §4.10).
4. **Statistical work stays in plain Python functions** (lifelines, pandas, numpy). Orchestration (routing + sequencing) is plain Python too — never graph-internal math.
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
churn-survival <node>        # run one node (node1..node5) or `map` onboarding
churn-survival run <raw>     # full pipeline: route -> Node 1 -> ... -> Node 5
churn-survival run <raw> --persist-run   # + persist run outputs under runs/<run_id>/
churn-survival gc            # retention/GC (runs, models, pending states, stale recovery)
churn-survival audit <id|--all>  # reproducibility audit: re-run + byte-diff persisted runs
churn-survival-api           # serve persisted runs (FastAPI/uvicorn)
```

## How to work here

1. Check `ROADMAP.md` for the current phase/task and its stated `Verification`.
2. Read only the architecture sections the task cites.
3. Implement, then prove the task's verification bar (usually fixture-based tests).
4. Update this file's status + ROADMAP checkboxes when a phase completes.

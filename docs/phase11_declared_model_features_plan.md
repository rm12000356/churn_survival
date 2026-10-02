# Phase 11 — Deployment-declared model features

Status: implemented 2026-10-02. Architecture §1.3a, §1.6 amendment, §1.8a and §2.6
are the authority; this file records the decisions and their reasons.

## Problem

Only the 8 keys of the `CoreFeatures` union could reach Node 2 (hard rule 5:
extras are never fed to a model). A new export therefore modeled tenure alone:
the e-commerce workbook (`data/raw/ecom.xlsx`, 5,630 rows, 17 feature columns)
ran Kaplan-Meier, "High" meant "tenure 18–19 months", and its one forced core
mapping (`CashbackAmount → monthly_charges`, LLM confidence 0.2) was wrong.
The 508 tenure-0 customers (53% churn) were `not_enough_data` because a
whole-month snapshot gives them a zero-length window.

## Decisions (owner, 2026-10-02)

| ID | Decision | Choice |
|---|---|---|
| D-F1 | Rule 5 wording | Typed `model_features` container; extras stay storage-only |
| D-F2 | Leakage strictness | Tiered: block on near-perfect separation / one-outcome presence; warn on weaker signals |
| D-F3 | Tenure 0 | Opt-in `months_before_midpoint` (T + ½ month for every row) |
| D-F4 | Migration | None; only ecom is re-onboarded. The 7 datasets keep byte-identical outputs |
| D-F5 | Missing feature values | Same as core: §1.7 threshold + complete-case in Node 2; derived configs with features enable passthrough so the customer stays visible |

## Design

- **Contract.** `CanonicalRecord.model_features: dict[str, float | str | None]`
  (omitted when empty → existing outputs unchanged). `Node1Config.declared_features`
  `{key: {kind, label}}` with load-time collision checks; `model_predictors` =
  core keys + sorted declared keys. `ProposedMapping.feature_kind`;
  `MappingConfig.approved_features` (key, kind, source column, label, screening
  snapshot) must match the report's `feature.<key>` targets exactly;
  `MappingConfig.supersedes`.
- **Authority.** The LLM only proposes `feature.<key>`. A person ticks each one;
  the API re-screens approved keys and rejects a block. Screening is
  deterministic Python, never the LLM.
- **Screening** (`router/feature_screening.py`): reuses
  `node1.feature_gate.evaluate_promotion` (its first production caller), adds the
  leakage tiers, a per-feature PH preview using Node 2's own `fit_cox` +
  `ph_test_p_values`, and reviewer facts. Thresholds in
  `config/feature_screening/v1.json`.
- **Node 1.** Mapping adapter writes approved values (typed by kind); feature
  gate demotes undeclared keys; Gate 8b mirrors Gate 8. No change when nothing
  is declared.
- **Node 2.** `node2.matrix.modeling_values(record)` = core ∪ model_features,
  used for kinds, specs, rows and cold-start. Declared values arrive typed, so
  the existing value-based kind inference encodes them as declared.
  `modeling_version` is **not** bumped: with no declared features every model is
  bit-identical, and declared features change the matrix (hence
  `training_dataset_version` / `model_version`) by themselves.
- **Identity.** `CODE_SEMANTICS_VERSION` 2026.10.5. Declared features live in the
  Node 1 config version and the mapping version, both already in the run identity.

## Findings from the ecom dry run

- All 17 columns approved → Node 2 PH test fails on `hour_spend_on_app`,
  `cashback_amount` and `prefered_order_cat` (p < 1e-5) → one stratified refit
  cannot absorb numeric violators → Kaplan-Meier. This is Node 2's frozen policy
  working as designed; the PH preview now warns about these columns before
  approval.
- Screening-ok columns only (7 features, midpoint tenure): `cox_ph` WARNING,
  c-index 0.73 (bootstrap 0.71–0.75), `complain` HR 2.07, High accounts span
  tenures 0.5–19.5 months.

## Known limits

- The PH preview is univariate; the multivariable fit can still violate PH.
- Missing-value indicators/imputation are not offered (complete-case only).
- Node 5 driver text uses the feature key, not `DeclaredFeature.label`.

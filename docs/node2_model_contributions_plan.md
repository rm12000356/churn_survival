# Node 2 model-contribution explanations (2026-10-01)

Status: **implemented.** Architecture amendments: §2.12b (Node 2 output) and
§4.4b (Node 4 driver policy).

## Problem

Node 4's `top_drivers` was a single model-wide list: every feature with HR > 1,
sorted by coefficient. Every account got that same list, and Node 5 showed it
as if it were evidence about that account (REVIEW.md L26). On dataset 6, all
4,296 ranked accounts showed `["plan_tier_starter", "support_tickets_90d"]`,
including a coefficient whose CI contains 1.0 (`support_tickets_90d`,
p = 0.77).

## Boundary (locked)

```
Cox model
  → Node 2  explains the fitted model   (per-customer contributions to relative log-hazard)
  → Node 4  selects qualifying drivers  (config-driven policy)
  → Node 5  renders deterministic text
  → optional LLM polish (rephrase only, validated)
```

Drivers are explanation metadata. They never feed a score, level, rank, or
confidence value.

## Mathematics

Encoded CoxPH: `LP_i = Σ_j β_j · x_ij`, and `risk_score = 1 − S(t_ref | x_i)`
is monotone in `LP_i`.

The model reference profile is a constructed point, not an observed customer
and never called "portfolio average". It puts numerics at their training mean
and categoricals at their reference category.

```
ref_j                 = mean(fit_data[j])     numeric
ref_j                 = 0                     categorical dummy (reference category)
contribution_ij       = β_j · (x_ij − ref_j)
baseline_log_hazard   = Σ_numeric β_j · ref_j
relative_log_hazard_i = Σ_j contribution_ij = LP_i − baseline_log_hazard
```

Terminology is locked everywhere (code, fields, docs, report text): hazard,
log-hazard, partial hazard, hazard ratio, contribution to relative log-hazard.
Never "relative risk", and never "risk" for a contribution. The run-wide lift
phrase "…times the portfolio average" is a genuine base rate and is unchanged.

## Decisions

| ID | Decision |
|---|---|
| C-1 | Node 2 emits `customer_contributions`, `customer_relative_log_hazard`, `baseline_log_hazard` (scored-aligned, full precision, CoxPH only). |
| C-2 | Records only for fitted predictor columns. Numerics always; a categorical dummy only when active; the reference category has none. A stratifying feature has none. |
| C-3 | `reliable` = the CI excludes 1.0. Node 2 never filters on it; it is provenance only. |
| C-4 | Node 4 policy: `contribution > 0` and (`reliable` when `drivers_require_reliable`). Sort by contribution descending, then feature ascending. Cap at `top_drivers_max`. Unreliable rows are dropped from both `top_drivers` and `driver_details`. |
| C-5 | **`per_customer_drivers` flag (deviation from plan v3).** The plan added only `drivers_require_reliable`, but also required v1–v3 to stay byte-identical. Once Node 2 always emits contributions, those configs would otherwise switch to per-account drivers too. A second flag (default `false`, on in v4) keeps v1–v3 bit-identical for any Node 2 input. |
| C-6 | The legacy model-wide D-2 list is used when Node 2 has no contributions (older outputs, Kaplan-Meier). |
| C-7 | Node 5 wording lives in `node5/report/driver_text.py` and is shared by evidence, the template summary, and HTML. The template's overall claim ("rates this account's churn hazard above the model reference profile") is made only when the account's total `relative_log_hazard > 0`. A positive top driver can coexist with a negative total. |
| C-8 | **Template summary may run to five sentences** when a driver sentence is present (otherwise four), so the support sentence is not cut. The architecture says "normally 2 to 4". |
| C-9 | The explainer prompt includes `driver_details`/`relative_log_hazard` only when present, so older prompts are unchanged. The validator registers driver values, reference values, hazard ratios, contributions, and the total as allowed numbers. Invented numbers are still rejected. |

## Versioning

| Item | Change |
|---|---|
| `config/node2/v1.json` `modeling_version` | 1.2.0 → 1.3.0, so `model_version` changes once. The fit is unchanged (`FIT_ALGORITHM_VERSION` stays `fit-2`). |
| `config/node4/v4.json` | New: v3 + `per_customer_drivers: true`, `drivers_require_reliable: true`, `ranking_version: "1.3"`. |
| Node 4 default | "3" → "4" in `run_pipeline`, the `run`/`node4` CLIs, and `POST /runs`. |
| `CODE_SEMANTICS_VERSION` | 2026.10.2 → 2026.10.3, so every `run_id` changes once. The audit skips old runs as `SEMANTICS_CHANGED`. |

## Verification (2026-10-01)

- **Dataset 6** (`dataset6_saas_churn_messy.csv`, no support):
  - v3 and v4 decisions are identical (customer, score, level, rank, confidence; `summary_stats`; churned list).
  - Driver sets: v3 had 1 distinct set; v4 has 12.
  - `support_tickets_90d` (CI [0.965, 1.049]) appears on no account.
  - Driver frequency: `usage_frequency` 1985, `plan_tier` 1844, `contract_length_months` 1654.
  - 1620 accounts have no positive driver (all their contributions are ≤ 0).
  - Lowest-usage account ACCT-3667 (High): `usage_frequency` 0 vs reference 3.592, contribution +0.610; then `plan_tier = starter` +0.348; then `contract_length_months` 1 vs 9.44, +0.182.
- **Dataset 7**, without and with support threads:
  - Decisions are identical under v3 and v4.
  - Driver sets: 36 distinct.
  - `support_tickets_90d` is reliable here (CI [1.068, 1.222]), so it correctly appears.
- **Determinism:** two full runs on each dataset produce byte-identical `node1..node5` output and `report.html`.

`support_tickets_90d` disappearing from dataset 6 drivers is correct
statistically, not a bug.

## Out of scope

Node 1 and Node 3, Node 3 evidence wording, PDF, imputed or fuzzy
contributions, and frontend display of `driver_details`. The frontend still
reads `top_drivers`, which is now per-account.

# Phase 10 — Forward-looking risk, lift-based levels, churned split, per-customer confidence

**Status:** implemented 2026-10-01. Owner-approved contract amendments to the frozen
Nodes 2 and 4 (additive, versioned). Node 4 **v3** is the new default; v1/v2 keep
their decisions bit-identical.

## Problem

Every completed run put all customers in Low, with one confidence value for the
whole run. Three stacked causes:

1. **Wrong quantity.** §4.4 used `1 − S(90d)`: the probability of churning in the
   first 90 days of tenure, measured from day 0. For an active customer already
   400 days in, that is meaningless (≈0.02 on datasets 6/7; exactly 0.0 on
   Cell2Cell, whose minimum tenure is 6 months).
2. **Absolute thresholds.** Medium needs 0.40 and High 0.70, i.e. a 40%/70%
   chance of churning within 90 days. Even the correct forward probability has
   a 99th percentile of 0.03–0.11 on the onboarded data.
3. **Already-churned customers were ranked** as at-risk accounts (20,609 of
   71,047 on Cell2Cell).

With support threads, Node 3 drove every non-Low level, and the two critical
rules that need high quantitative risk could never fire.

## Decisions

### D-R1 — Forward quantity (Node 2, additive)

Node 2 emits, per available horizon `t`, the conditional survival
`S(T + t) / S(T)` for each scored customer at current tenure `T`
(`Node2Output.forward_survival`, aligned to the scored subset), plus
`customer_tenure_days` / `customer_event_observed` (parallel to `customer_ids`)
and `max_follow_up_days`.

- CoxPH: `predict_survival_function(..., conditional_after=T)` (lifelines
  0.30.3; stratified predictions are reordered back to row order).
- Kaplan-Meier: the product over the customer's curve's `event_table` in
  `(T, T + t]` — the fitted `survival_function_` only holds the fit-time
  timeline points, so it cannot be used.
- CI: Greenwood log-log over the same window. KM uses the customer's own
  curve; Cox reuses the global KM Greenwood sum centred on the Cox point
  (`ci_approximate = true`, same convention as the existing Cox bands).
- `None` for churned customers and when `T + t > max_follow_up_days` (no tail
  extrapolation).
- The fit is unchanged. `modeling_version` → 1.2.0. That is part of the
  `model_version` payload, so `model_version` changes once.
  `CODE_SEMANTICS_VERSION` → `2026.10.2`, so every `run_id` changes once.

### D-R2 — Lift scale (Node 4 v3, `risk_norm_v2`)

```
p_i   = 1 − forward_S90_i
base  = mean(p) over active scored customers with a forward value (once per run)
lift  = p_i / base
risk  = piecewise_linear(lift, [[0,0],[1,0.20],[1.5,0.40],[3,0.70],[6,1.0]])
```

1.5× the book average lands exactly on the existing Medium threshold and 3× on
High, so `risk_thresholds`, `quantitative_thresholds` and the §4.10 critical
rules are unchanged. If fewer than `base_rate_min_customers` (30) qualify, the
base is 0, or Node 2 has no forward output, the whole run falls back to the
absolute scale with an explicit warning.

**Note:** with support supplied, `combined = 0.6·quant + 0.4·qual`. Quantitative
risk alone therefore reaches Medium (at most 0.60), not High. High needs a
qualitative signal too, or comes from the critical rules. In quantitative-only
runs (§4.14a), `combined = quant`, so the model produces High on its own.

### D-R3 — Churned split

With `separate_churned`, customers whose `event_observed == 1` go to
`Node4Output.churned_accounts` (`customer_id`, `tenure_days`,
`evidence_refs.node2`). They are not ranked, not counted in `risk_distribution`
or `n_customers`, and are counted in `SummaryStats.n_churned`. The universe
rule becomes ranked ∪ insufficient ∪ churned, with each customer in exactly one
list. Node 5 validates the three-way disjointness.

### D-R4 — Per-customer confidence (`conf_v2`)

```
model     = QUANT_CONFIDENCE_BY_STATUS[model_status]          # run ceiling
precision = 1 − clamp(ci_width / 0.20)    (None CI → 0.5)
history   = 0.5 + 0.5 · min(1, tenure / 180)
quant_i   = model × precision × history
```

- Quantitative-only runs use `quant_i` alone.
- With support, the existing weighted formula applies, with Node 3's
  `overall_signal_confidence` as the support term.
- A customer with no quantitative estimate, or with a D-6 partial alignment,
  gets `quant_i = 0`.
- The breakdown is published as `RankedAccount.confidence_factors`.

A forward CI at least 0.20 wide gives precision 0, and so confidence 0 in a
quantitative-only run. This is by design: the estimate is too imprecise to
trust. It mostly hits small risk sets far into tenure on Kaplan-Meier fallbacks
(Telco: 46 High accounts; Cell2Cell: 9).

### D-R5 — Tail customers are missing, never Low

An active scored customer with no forward value (window past follow-up) gets
`normalized_risk = None` and `forward_status = "beyond_follow_up"`. That makes
them insufficient data unless support yields a qualitative score. It is not
`PARTIAL_ALIGNMENT`. One run warning gives the count, and Node 5 adds a
data-quality note.

## Verification (2026-10-01, `LLM_PROVIDER=none`)

| Dataset | Model | Critical | High | Medium | Low | Insufficient | Churned | Confidence range |
|---|---|---|---|---|---|---|---|---|
| dataset 7 (no threads) | cox_ph | 0 | 27 | 774 | 2716 | 743 | 420 | 0.34–0.65 |
| dataset 7 + threads | cox_ph | 203 | 4 | 166 | 3498 | 389 | 420 | 0.00–0.77 |
| dataset 6 | cox_ph | 0 | 27 | 915 | 3354 | 304 | 400 | 0.34–0.67 |
| Telco | kaplan_meier | 0 | 67 | 937 | 3531 | 639 | 1869 | 0.00–0.44 |
| Cell2Cell | kaplan_meier | 0 | 9 | 2725 | 47666 | 38 | 20609 | 0.00–0.45 |

Dataset 7 with threads, v2 → v3:
- All 245 v2 Critical accounts are either still Critical (198) or now listed as churned (47).
- 5 new Critical accounts come from `critical_repeated_high_severity_plus_high_quant` (12 firings).
- 160 High/Medium accounts carry a quantitative reason.

Two persisted runs are byte-identical, and `churn-survival audit` passes.

Telco falls back to Kaplan-Meier because of the earlier REVIEW N-H1 refit PH
re-test, not because of this phase. Cell2Cell is Kaplan-Meier-only (no approved
predictors), so its risk varies by tenure alone. Adding predictors is a
separate onboarding step.

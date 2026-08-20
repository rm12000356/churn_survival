# Dataset 7 — Comprehensive Reference (Spec v1.2)

This is the single source of truth for the Dataset 7 synthetic diagnostic corpus.
It documents every case the corpus covers, the real numbers measured from the
generated artifacts (stable under the pinned golden hashes), the truth-schema,
the honest gaps it does *not* cover, and the deviations from the originally
planned outcomes that surfaced when the real pipeline ran.

> History: this document began as the *Dataset 7 — Specification v1.2 Addendum*
> (the deviations-only changelog) and was expanded into the full reference below.

**Scope.** `data/raw/dataset7_customers_messy.csv`,
`data/raw/dataset7_support_threads_messy.json`,
`data/ground_truth/dataset7_ground_truth.json`,
`scripts/generate_dataset7.py` (generator v1.1, spec 1.2),
`scripts/validate_dataset7.py` (58 checks), the router fixtures, and the
`tests/dataset7/` suite.

---

## Table of contents

1. [Overview & artifacts](#1-overview--artifacts)
2. [Reproduction](#2-reproduction)
3. [Generative model (DGP)](#3-generative-model-dgp)
4. [Covariate plan](#4-covariate-plan)
5. [Node 2 index bands](#5-node-2-index-bands)
6. [Support-data bands](#6-support-data-bands)
7. [Cohort map (cases covered)](#7-cohort-map-cases-covered)
8. [Support threads: messiness & placement](#8-support-threads-messiness--placement)
9. [Invalid-row taxonomy](#9-invalid-row-taxonomy)
10. [Node 1 reality](#10-node-1-reality)
11. [Node 2 reality](#11-node-2-reality)
12. [Node 3 oracle](#12-node-3-oracle)
13. [Node 4 oracle](#13-node-4-oracle)
14. [Node 5 trap oracle](#14-node-5-trap-oracle)
15. [Truth schema reference](#15-truth-schema-reference)
16. [Coverage mapping](#16-coverage-mapping)
17. [Known gaps / not covered](#17-known-gaps--not-covered)
18. [Deviations from the plan](#18-deviations-from-the-plan)
19. [Validator (58 checks)](#19-validator-58-checks)
20. [Determinism & golden hashes](#20-determinism--golden-hashes)
21. [Router fixtures](#21-router-fixtures)

---

## 1. Overview & artifacts

`dataset7_churn_diagnostic` is a deterministic end-to-end diagnostic corpus for
Nodes 1 → 5. A single master seed, no wall-clock time, no `datetime.now()`:
re-running the generator reproduces every artifact byte-for-byte (see
[§20](#20-determinism--golden-hashes)).

| Artifact | Contents | Size |
|---|---|---|
| `data/raw/dataset7_customers_messy.csv` | 15-column messy customer export | 5,000 rows (4,550 valid + 450 deliberately invalid) |
| `data/raw/dataset7_support_threads_messy.json` | per-customer support threads | 5,730 threads / 12,434 messages |
| `data/ground_truth/dataset7_ground_truth.json` | per-row oracle + scenario oracles | 21 top-level keys |

Fixed top-level parameters: master seed `2137457950`, reference date
`2026-08-15`, `N_VALID = 4550`, `N_INVALID = 450`, `N_RAW = 5000`, target event
band `420..520`, specification `1.2`, generator version `1.1`.

Key headline numbers (all asserted by the validator / tests):

- **Events:** 420 (in-band). **Threads:** 5,730. **Messages:** 12,434.
- **Unsupported-language threads:** 45. **Cross-channel duplicate pairs:** 25.
- **Tickets reconciled** (planned ≠ actual in-window count): 64 customers.
- **Missingness:** 495 valid customers (135 usage MCAR, 118 tickets MAR
  enterprise, 253 contract MNAR starter-monthly); 4,055 complete-case.
- **Node 1:** `PARTIAL accepted=4680 rejected=320`. **Node 2:** cox_ph,
  4,055 customers / 353 events, c-index 0.7606, `ph_severity=none`.

## 2. Reproduction

```bash
# Regenerate all three artifacts (runs the 58-check validator in-process;
# writes via staging + os.replace, so a failed self-check keeps old files).
python scripts/generate_dataset7.py

# Standalone validator (any subset of paths; defaults to the three artifacts).
python scripts/validate_dataset7.py
# -> Result: PASS (58/58 checks)

# Node 1 E2E on the messy file (routes via the confirmed mapping adapter).
churn-survival node1 data/raw/dataset7_customers_messy.csv --config dataset7
# -> PARTIAL  accepted=4680  rejected=320

# Node 2 E2E (Node 1 in-process + survival model).
churn-survival node2 data/raw/dataset7_customers_messy.csv --config dataset7
# -> cox_ph WARNING (4055 scored, 353 events, horizons 30/90/180)
```

The `tests/dataset7/test_dataset7.py` suite pins the golden hashes and asserts
the Node 1 split, the Node 2 directions, the 58-check validator, the invalid-row
taxonomy, and the router fixtures.

## 3. Generative model (DGP)

**Weibull proportional hazards with frailty** (locked):

```
lp_i = 0.85*I[starter] + 0.25*I[pro] - 0.06*contract_length_months
     - 0.18*usage_frequency + 0.10*support_tickets_90d + frailty_i
frailty_i ~ N(0, 0.15^2)
S(t) = exp(-(t/lambda0)^k * exp(lp_i)),  k = 1.2
```

- `lambda0` is locked at **36.0 months** in the DGP design, but the locked value
  with the exact coefficient set yields ~1,239 events — far above the 420–520
  target band. The generator retries fresh covariate draws (seeds
  `MASTER_SEED+1+attempt`, attempt 0–99) and, when none land in band,
  deterministically bisects `lambda0` on the accepted stream (seed
  `MASTER_SEED+1+100`). Result: `lambda0 = 61.605846` months, **420 events,
  attempt 100**.
- **§31 time-varying usage effect.** For the PH-violation cohort (indices
  `350..399`) the usage coefficient is cut to `BETA_USAGE * 0.5` beyond the
  180-day knot. Cumulative hazard is continuous at the knot:

  ```
  H(t) = (t/scale)^k * exp(lp)                      for t <= 180d
  H(t) = H(180d) + ((t-180d)/scale)^k * exp(lp_post) for t > 180d
  lp_post = lp - (1 - 0.5) * BETA_USAGE * usage_frequency
  S(t) = exp(-H(t))
  ```

- **Quantitative score** (`expected_quant_score`): for each *eligible* customer
  (Node 2 status `READY` or `WARNING`, indices `350..4549`, 4,200 customers)
  it is the empirical quantile of `exp(lp_i)` among the eligible subset —
  `P(exp(lp_j) <= exp(lp_i))`. It is `None` for the first 350 customers
  (INSUFFICIENT_DATA 200 / FALLBACK 100 / FAILED 50).

**Measured univariate event rates** (validated directions; `support_tickets`
Pearson corr with event `0.058`):

| Feature | Bucket | Rate |
|---|---|---|
| `plan_tier` | starter | 0.1713 |
| | pro | 0.0502 |
| | enterprise | 0.0198 |
| `contract_length_months` | 1 | 0.1803 |
| | 12 | 0.0674 |
| | 24 | 0.0215 |
| `usage_frequency` | low (≤1.5) | 0.2401 |
| | mid (1.5–4.5) | 0.0910 |
| | high (>4.5) | 0.0304 |
| `support_tickets_90d` | 0 | 0.0924 |
| | 3+ | 0.1915 |

The generator asserts the monotonic orderings (starter > pro > enterprise,
contract 1 > 12 > 24, usage low > mid > high, tickets 0 < 3+) before writing.

## 4. Covariate plan

**Random draws** (seeded, fixed order): tier `[starter 0.42, pro 0.33,
enterprise 0.25]`; contract per tier (`starter` 1/12 with 0.65/0.35; `pro`
1/12/24 with 0.20/0.55/0.25; `enterprise` 1/12/24 with 0.05/0.30/0.65); usage =
tier mean (3.0/4.0/5.5) + N(0, 1.5), clipped 0–7, rounded 0.1; uniform `u`;
frailty; status representation index; `last_login` (churned 30–120d, active 0–8d);
`legacy_flag` 20%; region; sales rep; internal-note 6%; decoy A/B indices.

**Per-band deterministic overwrites** (the case scaffolding in `_overwrite`):
see the [band table](#5-node-2-index-bands) and
[cohort map](#7-cohort-map-cases-covered) for what each index range pins.

**Messiness of the CSV surface** (what the mapping adapter must survive):

- **4 date formats** cycled by row index: `%Y-%m-%d`, `%m/%d/%Y`, `%d-%m-%Y`,
  `%Y.%m.%d` (the dot format required adding `%Y.%m.%d` to the adapter
  date-formats whitelist).
- **5 churn representations** (`Churned`/`Yes`/`1`) and **5 active
  representations** (`Active`/`No`/`0`), cycled per row.
- **Extras (must stay extra, never core):** `last_login_days_ago` (leakage
  decoy), `region`, `sales_rep`, `internal_notes`, `legacy_flag`, `decoy_a`,
  `decoy_b`. Validator checks 32/33 assert `last_login` is always extra and the
  decoy columns never reach core.

## 5. Node 2 index bands

Index band | Node 2 status / reason | Count | Measured events (rate) | Horizons
|---|---|---:|---:|---|
| 0–119 | `INSUFFICIENT_DATA` / `cold_start` | 120 | 0 (0.000) | `[]` |
| 120–199 | `INSUFFICIENT_DATA` / `short_tenure_15_29d` | 80 | 0 (0.000) | `[]` |
| 200–224 | `FALLBACK` / `qual_only` | 25 | 0 (0.000) | `[]` |
| 225–299 | `FALLBACK` / `fallback_generic` | 75 | 4 (0.053) | `[]` |
| 300–349 | `FAILED` / `failed_zero_variance` | 50 | 0 (0.000) | `[]` |
| 350–399 | `WARNING` / `ph_violation` (time-varying cohort) | 50 | 3 (0.060) | `[30, 90]` |
| 400–649 | `WARNING` / `short_tenure_30_90d` | 250 | 3 (0.012) | `[30, 90]` |
| 650–749 | `WARNING` / `warning_generic` | 100 | 7 (0.070) | `[30, 90]` |
| 750–4549 | `READY` / `ready` | 3,800 | 403 (0.106) | `[30, 90, 180]` |

Status totals: `INSUFFICIENT_DATA 200`, `FALLBACK 100`, `FAILED 50`,
`WARNING 400`, `READY 3,800`. Horizon distribution: `[]` 350, `[30,90]` 400,
`[30,90,180]` 3,800. The failed-corruption marker (`internal_notes =
"injected_corruption:required_feature_NaN_simulated"`) is placed on the `FAILED`
band (300–349).

## 6. Support-data bands

The Node 3 `expected_support_data_status` is assigned per index:

| Support status | Indices | Count |
|---|---:|---:|
| `no_data` | 0–89 and 770–2499 | 90 + 1,730 = 1,820 |
| `limited_data` | 90–769 and 2500–2829 | 680 + 330 = 1,010 |
| `sufficient_data` | 2830–4549 | 1,720 |

## 7. Cohort map (cases covered)

Every `support_cohorts` label, with index range, count, thread plan, and oracle
intent. (Counts are the measured `cohort_counts` from the truth; a customer can
carry several cohorts.)

| Cohort | Indices | Count | Thread plan | Oracle intent |
|---|---|---:|---|---|
| `cold_start` | 0–119 | 120 | generic in-window × tickets | Node 2 INSUFFICIENT_DATA |
| `short_tenure` | 120–199, 400–649 | 330 | generic in-window | Node 2 INSUFFICIENT/WARNING short tenure |
| `quant_only` | 770–969 | 200 | **no threads** (`no_data`) | high quant only; scores 0.9269–0.9667 |
| qual-only strong (no label; see `strong_cancellation_intent`) | 200–224 | 25 | cancel_strong in-30d | FALLBACK, quant `None` → critical rule_1 + `missing_quantitative_data` |
| `low_information` | 2500–2799 | 300 | 2 × low_info (in + out window) | Node 3 `low_information` flag |
| `conflict_a` | 2830–2879 | 50 | 2 × positive | happy on paper, churns by surprise → high `quantitative_qualitative_conflict` |
| `conflict_b` | 2880–2929 | 50 | cancel_moderate + positive out-window | says cancel, numbers say stay → low `quantitative_qualitative_conflict`; 2880–2909 (30) also rule_2 |
| `strong_cancellation_intent` | 2930–3019 (+200–224, +3850–3859) | 125 | cancel_strong in-30d | critical rule_1 |
| `moderate_cancellation_intent` | 3020–3079 | 60 | cancel_moderate in-30d | critical rule_1 |
| `weak_cancellation_intent` | 3080–3119 | 40 | cancel_weak in-30d | medium/low by quant |
| `renewal_concern` | 3120–3239 | 120 | 2 × renewal | Node 3 renewal flag; 3120–3139 (20) rule_3 |
| `repeated_issues` | 3240–3339 | 100 | repeated in-window + 2 out-window | Node 3 repeated flag; 3240–3254 (15) rule_4 |
| `high_urgency` | 3340–3419 | 80 | urgent in-window + out-window | Node 3 urgency flag, escalation signal |
| `positive_sentiment` | 3420–3819 | 400 | 2 × positive out-window | low risk, `positive_feedback` |
| `trap_001` | 3820–3829 | 10 | positive out-window | MEDIUM `usage_drop` trap |
| `trap_002` | 3830–3839 | 10 | complaint in-window | HIGH `billing_complaint` trap |
| `trap_003` | 3840–3849 | 10 | complaint + positive | LOW `conflicting_threads` trap |
| `trap_004` | 3850–3859 | 10 | cancel_strong in-30d | CRITICAL `critical_cancellation` trap (also strong intent) |
| `cross_channel_duplicate` | 3860–3884 | 25 | duplicate_issue pair (out-window + `is_dup`) | collapse to one ticket |
| `unsupported_language` | 3885–3914 | 30 | es/de/fr out-window | 45 unsupported threads, Node 3 `FAILED` |
| background (no label) | 3915–4549 | 635 | generic in-window × tickets + out-window | sufficient data, READY |
| `no_data` | — | 1,820 | — | Node 3 status `no_data` |

Quant-flagged cohorts (`quant_cohorts`): `quant_only` 200, `conflict_a` 50,
`conflict_b` 50.

## 8. Support threads: messiness & placement

**Channels.** `email 55% / chat 25% / phone 15% / twitter 5%`, with a 5% chance
of the deliberate typo `email → emial`, `chat → chatt`. Measured channel counts:
email 2,967, emial 150, chat 1,366, chatt 116, phone 858, twitter 273
(5,730 total).

**Placement.** `in_30d` (last 30 days before `observation_end`) for all
cancellation threads; `in_window` (last 90 days) for generic/in-window threads;
`positive` (180–120 days before end) for positive-sentiment threads; `out_window`
(strictly before the 90-day ticket window) for everything that must *not* count
as a 90-day ticket. Every thread and message timestamp lies inside the customer's
observation window, and every cancellation thread is within 30 days of the end
(validator checks 9–12).

**Message pools** by intent: `generic`, `cancel_strong`, `cancel_moderate`,
`cancel_weak`, `cancel_neutral` (pool defined, not placed), `positive`,
`renewal`, `repeated`, `urgent`, `complaint`, `low_info`, `duplicate_issue`,
and `unsupported_{es,de,fr}`. The pools deliberately avoid the validator's
`CANCEL_KEYWORDS` unless the thread is meant to be a cancellation thread.

**Structural messiness per thread:** `subject=None` on 10% of threads plus all
`low_info` threads; `status=None` on 15%; `tags` `None` 15% / `[]` 10% /
`[""]` 5% / 1–2 tags otherwise.

**Cross-channel duplicates.** 25 pairs: an email thread plus a `chatt` thread on
the same issue; the truth records `collapsed_thread_id` / `surviving_thread_id`
so Node 3 must collapse them (the collapsed thread never counts as a 90-day
ticket).

**Unsupported languages.** 45 threads in es/de/fr (30 customers). These are the
Node 3 `FAILED` thread cohort (`n_failed_threads` per customer), giving
`node3_processed_threads = 5685`, `node3_failed_threads = 45`.

**Ticket reconciliation.** After threads are placed, the *actual* in-window
count (excluding collapsed threads) is recomputed and stored as the core feature
`support_tickets_90d`; the planned count stays the DGP covariate in
`dgp.tickets_planned`. 64 customers (events with tenure < 90 days, where no
"outside-window" region exists) have reconciled values.

## 9. Invalid-row taxonomy

450 deliberately invalid rows exercise 8 taxonomy codes. Node 1 precedence
(defense-in-depth ordering) is `DUPLICATE_ID > FUTURE_START_DATE >
FUTURE_END_DATE > IMPOSSIBLE_TENURE > NEGATIVE_TENURE > BAD_EVENT_VALUE >
INVALID_PLAN > MISSING_CORE`.

| Code | Count | Row shape | Node 1 gate exercised |
|---|---|---:|---|
| `FUTURE_START_DATE` | 60 | signup after reference date | WINDOW_ORDER + TENURE_INVALID |
| `FUTURE_END_DATE` | 50 | cancellation after reference date | FUTURE_LEAKAGE |
| `BAD_EVENT_VALUE` | 40 | status not in {0,1} vocabulary | EVENT_OBSERVED |
| `DUPLICATE_ID` | 80 | reuses valid customer IDs | UNIQUE_ID |
| `MISSING_CORE` | 100 | blank usage (40) / tickets (30) / contract (30) | **rescued by passthrough** |
| `IMPOSSIBLE_TENURE` | 60 | cancellation ≥ 2 days before signup | WINDOW_ORDER + TENURE_INVALID |
| `NEGATIVE_TENURE` | 30 | cancellation exactly 1 day before signup | WINDOW_ORDER + TENURE_INVALID |
| `INVALID_PLAN` | 30 | plan value outside vocabulary + blank usage | **rescued by passthrough** |

**Measured outcomes** (`expected_node1_outcome`): `rejected:WINDOW_ORDER` 150
(60 future-start + 60 impossible + 30 negative tenure), `rejected:FUTURE_LEAKAGE`
50, `rejected:EVENT_OBSERVED` 40, `rejected:UNIQUE_ID` 80,
`accepted:missingness_passthrough` 130 (100 MISSING_CORE + 30 INVALID_PLAN).

The 130 rescued rows are accepted with a null core and flow through to Node 2's
complete-case rule. The 320 actual rejections surface as **470 gate-error
records** because the 150 tenure-corrupted rows each trip both the `WINDOW_ORDER`
and `TENURE_INVALID` gates.

## 10. Node 1 reality

The messy file routes via the confirmed mapping adapter
`mapping:e6bfd1745c54` (`config/mappings/map_20260820T000000Z.json`) using the
audited transform ops; the deployment config is
`config/node1/vdataset7.json` (validation_version `1.1.0`,
`allow_missing_core_passthrough: true`, `missingness_threshold: 0.3`,
`router_high_confidence_threshold: 0.8`, approved core keys
`plan_tier` / `contract_length_months` / `usage_frequency` /
`support_tickets_90d`).

**E2E result (measured):**

```
PARTIAL  accepted=4680  rejected=320
WINDOW_ORDER 150  TENURE_INVALID 150  UNIQUE_ID 80
FUTURE_LEAKAGE 50  EVENT_OBSERVED 40
missingness_passthrough:
  contract_length_months 283 (253 valid + 30 invalid)
  support_tickets_90d    148 (118 valid + 30 invalid)
  usage_frequency        205 (135 valid + 70 invalid)
```

The §1.7 Amendment v1.2 passthrough rescues *every* blank-core row whose column
missingness is at or below the 30% threshold — including the 130 taxonomy rows
designed to be quarantined. `CORE_MISSING` is therefore **never emitted at batch
level** for this deployment (see [§17](#17-known-gaps--not-covered)).

## 11. Node 2 reality

Status-band oracle outcomes: `INSUFFICIENT_DATA` 200 (cold-start 120 +
short-tenure 15–29d 80), `FALLBACK` 100 (qual-only 25 + generic 75), `FAILED`
50, `WARNING` 400, `READY` 3,800.

The complete-case rule excludes all null-core records (the 495 §11-missing
customers plus the 130 rescued taxonomy rows): **4,055 customers scored,
353 events**. Fitted model (measured):

```
model_type  cox_ph   model_status  WARNING
n_customers 4055     n_events 353     c_index 0.7606
plan_tier_starter        +0.427
plan_tier_pro            -0.169   (DGP +0.25 — sign disagrees, collinearity persists)
contract_length_months   -0.0276
usage_frequency          -0.1365
support_tickets_90d      +0.1328
strata_used              None     ph_severity  none
```

Directions recovered: `plan_tier_starter` HR>1, `contract_length_months` HR<1,
`usage_frequency` HR<1, `support_tickets_90d` HR>1. `plan_tier_recoverability`
is honestly `partial` (pro not recovered).

## 12. Node 3 oracle

Per-customer `support_truth` fields (14 keys): status, thread counts
(`n_threads`, `n_processed_threads`, `n_failed_threads`), `tickets_90d`,
`thread_ids`, `key_themes`, `expected_flags`, churn-language detection,
signal strength, overall confidence, escalation signal, message count,
latest interaction.

**Measured distributions:**

- Threads per customer: 0 → 1,870; 1 → 613; 2 → 1,454; 3 → 358; 4 → 155;
  5 → 85; 6 → 15.
- Overall signal confidence (from thread count): `none` 1,870, `low` 613,
  `medium` 1,454, `high` 613.
- Signal strength: `strong` 305, `moderate` 270, `weak` 450, `none` 3,525.
- Escalation signal (`high_urgency` or `repeated_issues`): true 180.
- Flags (11-vocabulary): `strong_cancellation_intent` 125, `low_information`
  300, `cancellation_intent_conflict` 50, `moderate_cancellation_intent` 60,
  `weak_cancellation_intent` 40, `renewal_or_contract_concern` 120,
  `repeated_issue` 100, `high_urgency` 80, `positive_feedback` 400,
  `cross_channel_duplicate` 25, `unsupported_language` 30.
- Key themes: `cancellation_intent` 275, `positive_feedback` 450,
  `renewal_or_contract_concern` 120, `product_bug_or_outage` 100,
  `poor_support_experience` 80, `other` 3,525.
- `expected_churn_language_detected` is derived from the actual message text and
  cross-checked in validator check 53.

## 13. Node 4 oracle

Measured risk distribution (4,550 customers):
`critical` 250, `high` 911, `medium` 995, `low` 2,069, `insufficient_data` 325.

**Critical rules (architecture §4.10):**

| Rule | Reason type | Count | Cohort source |
|---|---|---:|---|
| rule_1 | `critical_cancellation_intent` | 185 | strong (125) + moderate (60) intent |
| rule_2 | `critical_cancellation_plus_significant_flag` | 30 | conflict_b with i < 2910 |
| rule_3 | `critical_high_quant_plus_contract_concern` | 20 | renewal 3120–3139 |
| rule_4 | `critical_repeated_high_severity_plus_high_quant` | 15 | repeated 3240–3254 |

`reason_types` add `missing_quantitative_data` whenever quant is `None` and Node 2
is not READY/WARNING (the qual-only strong cohort). The generator asserts the
quant bands before writing: conflict_a min ≥ 0.75, conflict_b max ≤ 0.25,
quant_only min ≥ 0.70 (measured band 0.9269–0.9667, all 200 ≥ 0.70).

## 14. Node 5 trap oracle

40 trap customers (`node5_trap_oracle`), 10 per trap. Each records
`trap_kind`, `truth_risk_level`, `forbidden_claims`, `narrative`, and
`expected_risk_level` (which equals the Node 4 oracle — validator check 50
ensures the synthesizer produces exactly the expected level).

| Trap | `trap_kind` | Truth | Forbidden claims |
|---|---|---|---|
| 001 | `usage_drop` | medium | high, critical, risk_due_to_usage_drop |
| 002 | `billing_complaint` | high | critical, cancellation_intent |
| 003 | `conflicting_threads` | low | high, critical |
| 004 | `critical_cancellation` | critical | low, medium, stays_active |

## 15. Truth schema reference

Top-level keys: `dataset`, `reference_date`, `metadata`, `generator`,
`dgp_truth`, `pipeline_expectations`, `directions`,
`observed_univariate_event_rates`, `cohort_counts`, `customer_truth`,
`invalid_rows`, `support_truth`, `node2_scenario_oracle`,
`node3_scenario_oracle`, `node4_scenario_oracle`, `node5_trap_oracle`,
`cross_channel_duplicates`, `validation_oracle`, `threads_summary`.

Per-customer `customer_truth[cid]`: `customer_index`, `canonical`
(observation_start/end, event_observed, tenure, reference_date), `core_features`,
`extra_features`, `support_cohorts`, `quant_cohorts`, `missingness` (keys with
mechanisms `MCAR` / `MAR_enterprise` / `MNAR_starter_monthly`), `node4_oracle`,
`pipeline_expectations.{node2,node3,node4}`, `dgp` (latent event time in months
and days, linear predictor, frailty, baseline lambda0, weibull shape,
`tickets_planned`, optional `time_varying`, optional `corruption`).

`metadata` carries the locked params (seed, reference date, counts, event
attempt, calibrated λ0). `generator` repeats them plus coefficients,
`events_generated`, `threads_generated`, `messages_generated`,
`unsupported_language_threads`, `cross_channel_duplicate_pairs`,
`tickets_reconciled_customers`, `missingness` summary, `quant_only_band`, and
`ph_cohort`. `dgp_truth` documents the distribution, baseline, coefficients,
frailty, and the full piecewise `time_varying_effect` formula.

## 16. Coverage mapping

Cases exercised end-to-end, mapped to the architecture/ROADMAP intent:

- **Node 2:** cold-start `INSUFFICIENT_DATA` (120); short-tenure 15–29d / 30–90d
  (80 / 250); `FALLBACK` qual-only (25) and generic (75); `FAILED` marker (50);
  time-varying-PH cohort (50); WARNING bands (400); READY full horizons (3,800);
  complete-case missingness exclusion (495 + 130); missingness thresholds.
- **Node 3:** R1–R4 signal taxonomy; low-information threads; conflicting threads
  (conflict_a/b); cross-channel duplicate collapse (25); unsupported languages
  → FAILED (45); churn-language detection; signal strength / confidence /
  escalation / theme derivation from actual message text.
- **Node 4:** critical rules R1–R4 with exact counts; explicit critical-only
  rules (never from score alone); `insufficient_data` as a first-class state;
  `missing_quantitative_data` when quant is null; the combined score never
  produces Critical on its own.
- **Node 5:** the four traps prove Node 5 cannot inflate on a usage trend /
  complaint / single thread and cannot downplay explicit cancellation intent;
  forbidden-claims disjointness is validated (check 51).
- **Router:** canonical-headered file → `clean_csv`; German-headered file →
  `UnmappedFormatError`; messy file → confirmed mapping adapter.

## 17. Known gaps / not covered

Honest coverage limits, all deliberate and documented:

- **Node 2's stratified-refit path is NOT exercised.** The structural
  `support_tickets_90d` time-varying effect is not detected as *serious* at the
  configured PH threshold, so this fit runs unstratified (`strata_used=None`,
  `ph_severity=none`). The PH-violation *cohort* exists (50 customers) but the
  refit machinery itself is not triggered by dataset7.
- **`CORE_MISSING` quarantine is never produced at batch level.** For this
  deployment the §11 blanks are always ≤30% per column and the passthrough
  rescues every one of them (including the 130 taxonomy rows). The quarantine
  code is only exercised by other deployments' tests, not by dataset7.
- **Node 1 has no plan-vocabulary gate.** The `INVALID_PLAN` taxonomy rows are
  only rejected-indirectly (via a blank usage cell → passthrough), so their
  "invalid plan" intent is **rescued**, not quarantined.
- **FAILED is a batch-level concept.** Node 2's `FAILED` status is batch-level,
  so the per-customer `FAILED` band (300–349) is *oracle intent* only — the
  corruption marker in `internal_notes` is what a real deployment would key on.
  Node 3's `FAILED` (unsupported language) is per-thread and genuine.
- **No numerical covariate with genuine, large missingness above threshold** —
  every blank column stays ≤ 30%, so the batch-level `COLUMN_MISSINGNESS`
  rejection path is not exercised by this dataset.
- **`cancel_neutral` and `low_info` pools:** `cancel_neutral` is defined but not
  placed in the thread plan; `low_info` is used only by the low-information
  cohort.

## 18. Deviations from the plan

### 18.1 Node 1 — 4680 accepted / 320 rejected (planned 4550 / 450)

The §1.7 passthrough rescues *every* blank-core row at or below the 30% column
threshold — including the 130 rows designed to be quarantined for
`MISSING_CORE` (100) / `INVALID_PLAN` (30). Those 130 rows are accepted with a
null core and flow through to Node 2's complete-case rule. Consequence: the 130
taxonomy rows no longer exercise the `CORE_MISSING` / `INVALID_PLAN` quarantine
codes; the taxonomy is still exercised by the 320 real rejections (5 codes), and
invalid-row outcomes record `accepted:missingness_passthrough` where applicable
(check 58).

### 18.2 Node 2 — cox_ph with NO stratification (planned stratified refit)

With the 130 rescued rows in the input, the old Node 2 prepared the encoding
from *all* records — including phantom plan values (`gold`, `platinum`, …) from
the rescued `INVALID_PLAN` rows — which produced all-zero CoxPH columns and broke
the fit. `node2/node.py` `_prepare` now builds the feature specs from the
**scored** (complete-case) subset, so only real observed categories are encoded.
The PH-violation refit then found **severity = none**, so `strata_used = None`
and the model is the unstratified CoxPH fit shown in
[§11](#11-node-2-reality). Truth records `plan_tier_recoverability = "partial"`,
`ph_severity = "none"`, `strata_used = None`; `directions.plan_tier` claims
`starter → recovered_higher_hazard` and `pro → no_reliable_adjusted_claim`.

### 18.3 Generator bug fix — `_bisect_lambda0`

The original bisection inverted its bracket for the piecewise baseline model:
`count(200) = 131 < 420` made the loop scale λ up forever. It now converges to
the largest λ with count ≥ min in 80 iterations (`lo, hi = 0.5, 400.0`). Result:
λ0 = 61.605846 months, 420 events, attempt 100.

### 18.4 Node 2 edge-case hardening

`encoding_scheme` (node2/node.py) guards `spec.categories[0]` for empty
categorical specs — an all-excluded dataset (everything `not_enough_data`) no
longer raises `IndexError` and still yields `model_type = none` /
`INSUFFICIENT_DATA`.

## 19. Validator (58 checks)

Six original groups plus the v1.2 additions:

- **Structural (1–6):** raw rows 5000, valid 4550, invalid 450, unique IDs,
  thread customer IDs exist, all dates parse.
- **Temporal (7–13):** start ≤ end, end ≤ reference, thread/message timestamps
  inside the window, cancellation threads within 30 days, no future dates.
- **Cross-node (14–17):** `support_tickets_90d` matches actual in-window
  threads (excluding collapsed), events + censored = valid, Node 3 processed +
  failed = threads, with-data + no-data = valid.
- **Cohorts (18–31):** minimum counts for strong/moderate/weak intent, repeated,
  no-data ≥ 1600, conflict_a/b ≥ 40, cold-start ≥ 100, insufficient-data routing
  ≥ 80, critical total ≥ 150, critical rule minimums, 25 duplicate pairs,
  exactly 45 unsupported-language threads, 40 trap customers.
- **Node 1 (32–35):** leakage always extra, decoy never core, one primary code
  per invalid row, taxonomy counts exact.
- **DGP (36–40):** every valid row has a latent event time, non-negative,
  event status agrees with censoring, tenure agrees with dates, event count in
  band.
- **v1.2 (41–58):** metadata + specification/generator versions, `dgp_truth`
  with time-varying flag, exactly-50 PH cohort (`CUST-0351..0400`), Node 2
  expectations (`expected_model=cox_ph`, recoverability in
  {stratified, partial, adjusted}, ticket/usage/contract recoverable), Node 1
  partial expectations (4680/320), CSV-blank ↔ truth missingness (via
  `rows[rec["customer_index"]]` with a header-name map so duplicate-ID
  overwrites can't hide), missingness rates within tolerance (usage 0.030,
  tickets 0.102, contract 0.211), quant-only band ≥ 0.70, qual-only strong =
  critical, trap expected == truth + forbidden-claims disjoint, flag vocab,
  churn-language consistency (pre-indexed by customer), signal-strength vocab,
  FAILED corruption marker, missingness count consistency (495), LF-only line
  endings, invalid-row outcomes.

## 20. Determinism & golden hashes

Artifacts are byte-identical across runs (LF-only line endings, atomic staging
writes via `_write_on_pass`, no wall-clock time). Golden SHA-256 pinned in
`tests/dataset7/test_dataset7.py`:

- CSV `BB7ADC382A13A34B47D12F537E7085877A1B1B9AD416ED8DBFBC5138D8FBF979`
- Threads `A4A274C7EF34024ED2D9D31C9A3F877B6088A66081BC46E211E1CC4A3FEB4DE3`
- Truth `AF30A2AAB68D97DE752AB8C02D207A0942A62224311CFDB6BDBEABAE811F2011`

## 21. Router fixtures

- `data/raw/dataset7_customers_modern.csv` — 10 valid rows with canonical
  headers; routes to the `clean_csv` adapter.
- `data/raw/dataset7_customers_german.csv` — 5 valid rows with German headers;
  no adapter matches and `run_node1` raises `UnmappedFormatError`.
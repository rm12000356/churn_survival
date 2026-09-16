# Phase 5 — Node 4 (Synthesis / Ranked Account List): Implementation Specification

**Status:** Spec closure complete (D-1…D-9 locked, §10); **not yet implemented**.
**Companion to:** `architecture.md` §4 (the authority), `ROADMAP.md` Phase 5 (Tasks 5.1–5.12).
**Branch at time of writing:** `feat/node4` (even with `main`; no Node 4 code committed).

**Workflow:** spec closure → implementation → independent adversarial QA →
remediation → QA of remediation → freeze.

This document describes *how* Node 4 is to be built. It is derived from the frozen
architecture contract (§4.0–§4.30) and the already-shipped repo scaffolding. If this
document and `architecture.md` ever disagree, **`architecture.md` wins**.

---

## 1. Purpose & Position

Node 4 deterministically merges:

- **quantitative** survival risk from **Node 2** (`Node2Output`, §2.12), and
- **qualitative** support signals from **Node 3** (`Node3Output`, §3.11)

into a ranked, explainable list of accounts (`Node4Output`, §4.24). It produces
decision-support structure only — the client-facing narrative is Node 5.

```
Node 1 → Data trust
Node 2 → Statistical risk
Node 3 → Qualitative evidence
Node 4 → Deterministic synthesis          ← this node
Node 5 → Client communication
```

Node 4 **does not** modify, reinterpret, or overwrite upstream outputs. Every
scoring, threshold, classification, ranking, evidence, and failure decision is
defined by §4 and driven by versioned configuration.

---

## 2. Current State of the Repository

Already present (do **not** recreate):

| Artifact | Location | Notes |
|---|---|---|
| Node 4 output contracts | `schemas/node4.py` | `RankedAccount`, `StructuredReason`, `QuantitativeInfo`, `QualitativeInfo`, `EvidenceRefs`, `SummaryStats`, `Node4ProcessingReport`, `Node4Output` |
| Reason vocabulary | `schemas/enums.py` | `ReasonType` (all 15, incl. the 4 critical types), `RiskLevel`, `Severity`, `FlagType`, `SignalStrength`, `OverallSignalStrength`, `CustomerState`, `ModelStatus`, `SupportDataStatus` |
| Node 4 config model | `config/models.py::Node4Config` | Frozen, `extra="forbid"`, weight-sum invariant already enforced |
| Config loader hook | `config/loader.py::load_node4_config` | `load_config(config_dir()/"node4"/f"v{version}.json", Node4Config)` |
| Empty package | `node4/__init__.py` | Empty |

Missing (the actual Phase 5 deliverables):

- `config/node4/v1.json` (the versioned config file).
- The `node4/` implementation modules.
- `tests/node4/`.
- `pipeline/main.py` dispatch (currently Node 4 is deliberately *unimplemented* and
  must fail loudly, per §6 / Task 0.8).

> **Regression note:** `tests/test_pipeline.py` asserts `main(["node4"]) == 1` with
> `"not implemented"`. When Node 4 lands, move that "not implemented" assertion to
> `node5` and add Node 4 to `IMPLEMENTED_NODES`.

---

## 3. Contracts

### 3.1 Inputs (§4.3)

```python
{"node2_output": dict, "node3_output": dict, "config": dict}
```

- `node2_output` → `Node2Output` (`schemas/node2.py`).
- `node3_output` → `Node3Output` (`schemas/node3.py`).
- `config` → `Node4Config` (validated, then frozen).
- Join key: **`customer_id`**.
- Universe: `set(node2.customer_ids) | {cs.customer_id for cs in node3.customer_signals}`.
- Every union member must appear exactly once in `ranked_accounts` **or**
  `insufficient_data_accounts`.

### 3.2 Reading Node 2 fields (alignment is critical)

`Node2Output` is alignment-based, not a list of per-customer dicts:

- `customer_ids: list[str]` — full universe, deterministic ascending order.
- `customer_states: list[CustomerState]` — parallel to `customer_ids`; values
  `scored` / `not_enough_data` / `excluded`.
- `risk_scores: list[float] | None` — covers **only scored** customers (the i-th
  scored customer is the i-th `customer_ids` entry whose state is `scored`).
- `survival_probabilities: dict[str, HorizonResult]` — per-horizon, keyed
  `f"{horizon}d"` (`"30d"`, `"90d"`, `"180d"`). Each `HorizonResult.values` is a
  list parallel to the scored subset; `ci` likewise; `status` is
  `AVAILABLE`/`INSUFFICIENT_DATA`.
- `model_status: ModelStatus` — **run-level**, not per-customer.
- `model_version: str`, `feature_associations`, `warnings`.

Consequences for Node 4:

1. Build a `customer_id -> index` map over `customer_ids`; a customer's
   `customer_state = customer_states[idx]`.
2. Extract per-customer `survival_prob_90d` from
   `survival_probabilities["90d"].values[i]` where `i` is the position of that
   customer in the **scored subset**, only when
   `survival_probabilities["90d"].status == AVAILABLE` and the value is not `None`.
3. Extract per-customer `risk_score` from the scored-subset index into `risk_scores`.
4. `model_status` applies to the whole run → used directly for
   `QUANT_CONFIDENCE_BY_STATUS` (§4.15).

### 3.3 Reading Node 3 fields

`node3_output.customer_signals: list[CustomerSupportSignals]`, each with
`customer_id`, `support_data_status`, `signal_strength` (`OverallSignalStrength`),
`overall_signal_confidence`, `churn_language_detected`, `escalation_signal`,
`risk_flags: list[AggregatedRiskFlag]` (each with `flag_type`, `severity`,
`signal_strength`, `confidence`, `recurrence_count ≥ 1`, `strongest_evidence`,
`evidence_message_ids`), `key_themes`, `meta`.

### 3.4 Output (§4.21–§4.24)

`Node4Output` (already defined):

```python
{
    "ranked_accounts": list[RankedAccount],
    "insufficient_data_accounts": list[RankedAccount],
    "summary_stats": {n_customers, n_critical, n_high, n_medium, n_low, n_insufficient_data},
    "processing_report": {"warnings": list[str], "errors": list[dict]},
}
```

`summary_stats` counts **must equal** the actual list lengths (§4.24).

---

## 4. Task 5.1 — Configuration Object (§4.2)

`Node4Config` already exists in `config/models.py` and is loaded by
`load_node4_config(version)`. The remaining deliverable is the versioned file
`config/node4/v1.json`:

```json
{
  "ranking_version": "1.0",
  "threshold_version": "1.0",
  "critical_rules_version": "1.0",
  "normalization_version": "risk_norm_v1.0",
  "top_drivers_max": 5,

  "quantitative_weight": 0.60,
  "qualitative_weight": 0.40,
  "agreement_bonus": 0.05,

  "risk_thresholds": { "medium": 0.40, "high": 0.70 },
  "quantitative_thresholds": { "low": 0.20, "medium": 0.40, "high": 0.70 },
  "confidence_weights": { "quantitative": 0.55, "qualitative": 0.45 },

  "hierarchy_weights": {
    "cancellation_intent": 1.00,
    "renewal_or_contract_concern": 0.85,
    "product_bug_or_outage": 0.70,
    "poor_support_experience": 0.70,
    "billing_complaint": 0.55,
    "feature_missing": 0.55,
    "usage_drop_related": 0.55,
    "competitor_mention": 0.45,
    "positive_feedback": 0.00,
    "other": 0.30
  },

  "strength_scores": { "weak": 0.33, "moderate": 0.66, "strong": 1.00 },
  "strength_order":  { "none": 0, "weak": 1, "moderate": 2, "strong": 3 },

  "reference_date": "2026-08-15"
}
```

Invariants already enforced by `Node4Config._weights_sum_to_one`:

- `quantitative_weight + qualitative_weight == 1.0`
- `confidence_weights.quantitative + confidence_weights.qualitative == 1.0`
- `hierarchy_weights[positive_feedback] == 0.00` (§4.2 rationale)
- All thresholds and scores within `[0, 1]`.

> **Prerequisite:** `normalization_version` and `top_drivers_max` are not yet
> fields on `config/models.py::Node4Config` (which is `extra="forbid"`). They must
> be added to the model (with `top_drivers_max: int = Field(default=5, ge=0)`)
> **before** `config/node4/v1.json` will load. This is safe to do now because
> `config/node4/v1.json` does not exist yet — it is not a released-version break.

**Verification (Task 5.1):** weight-sum invariant test; `normalization_version`
present; loaded config round-trips through `load_node4_config("1")`.

---

## 5. Module Map

Proposed new package layout under `node4/` (mirrors the plain-Python style of
`node2/` and `node3/`; no LangGraph inside the node):

```
node4/
├── __init__.py
├── node.py          # entry point + orchestration flow + CLI
├── quantitative.py  # §4.4  normalization + top_drivers
├── qualitative.py   # §4.5/§4.6 scoring, recurrence bonus, strongest signal
├── scoring.py       # §4.7  combined score + agreement bonus
├── rules.py         # §4.8–§4.13 significant flag, critical rules, level, insufficient
├── confidence.py    # §4.14/§4.15 confidence + missing-upstream behavior
├── reasons.py       # §4.11/§4.16 deterministic StructuredReason construction
├── evidence.py      # §4.23 evidence references
├── ranking.py       # §4.18–§4.20 sort + sequential ranks
└── explain.py       # §4.25 optional LLM explanation (no authority)
```

### 5.1 `node4/quantitative.py` — Task 5.3 (§4.4)

```python
def quantitative_risk_score(survival_prob_90d, risk_score) -> float | None:
    if survival_prob_90d is not None:
        return round(1.0 - survival_prob_90d, 3)
    if risk_score is not None:
        return normalize_risk_score(risk_score)
    return None
```

**Locked — D-1 `normalize_risk_score`** (`normalization_version = "risk_norm_v1.0"`).
Node 2's `risk_score` is *already* `clip(1 - S(t_ref), 0, 1)` (`node2/cox.py:103`),
so the normalization is an identity clamp, **not** a second scoring system:

```python
def normalize_risk_score(risk_score: float) -> float:
    return max(0.0, min(1.0, round(risk_score, 3)))
```

- `survival_prob_90d` path and the `risk_score` fallback must be **numerically
  identical when the 90d horizon is `AVAILABLE`** (both are `1 - S(90)`); assert
  this in a test. The fallback matters only when 90d is
  `INSUFFICIENT_DATA`, where `risk_score = 1 - S(median tenure)` (a valid risk
  probability on a different time base — record that time base).
- Result clamped/asserted to `[0.0, 1.0]`.
- **Missing quant score ≠ low risk** → `None`, never `0.0`.

**Locked — D-2 `top_drivers`.** Deterministic, never LLM-derived. From
`feature_associations` (only present when `model_type == cox_ph`): keep features
with `hazard_ratio > 1.0`, sort by `coefficient` descending, tie-break `feature`
ascending, cap at `top_drivers_max` (= 5). For `kaplan_meier` / `none` →
`top_drivers = []`.

### 5.2 `node4/qualitative.py` — Tasks 5.4 & 5.5 (§4.5, §4.6)

Base score (strongest flag wins; Node 4 does **not** reapply recency decay):

```python
qualitative_score = 0.0
for flag in customer.risk_flags:
    flag_score = hierarchy_weights[flag.flag_type] * strength_scores[flag.signal_strength]
    qualitative_score = max(qualitative_score, flag_score)
```

- `support_data_status == "no_data"` or no flags → `qualitative_score = 0.0`.
- `positive_feedback` weight `0.00` → can never reduce the score.

Recurrence bonus (§4.5.1):

```python
max_recurrence_count = max((f.recurrence_count for f in customer.risk_flags), default=0)
recurrence_bonus = min(0.20, 0.05 * max(0, max_recurrence_count - 1))
qualitative_score = min(1.0, qualitative_score + recurrence_bonus)
```

Bonus table: `1→0.00, 2→0.05, 3→0.10, 4→0.15, 5+→0.20`.

Strongest signal tie-breaks (§4.6), in order:

1. `strength_order` descending (`strong > moderate > weak > none`).
2. hierarchy weight descending.
3. `recurrence_count` descending.
4. `flag_type` lexicographic ascending (determinism guarantee).

`strongest_signal_strength` (used by the agreement bonus and reasons) is the
`signal_strength` of the flag selected by these rules.

### 5.3 `node4/scoring.py` — Task 5.6 (§4.7)

```python
combined_score = quantitative_weight * (quantitative_score or 0.0) \
               + qualitative_weight * qualitative_score

strong_agreement = (
    quantitative_score is not None
    and quantitative_score >= quantitative_thresholds["high"]
    and strongest_signal_strength == "strong"
)
if strong_agreement:
    combined_score += agreement_bonus

combined_score = max(0.0, min(1.0, combined_score))
```

The agreement bonus increases the score but **can never create Critical**.

### 5.4 `node4/rules.py` — Tasks 5.7, 5.8, 5.9 (§4.8–§4.13)

**Significant flag predicate (§4.8):**

```python
def is_significant(flag) -> bool:
    if flag.flag_type == "positive_feedback":
        return False
    return flag.severity in {"medium", "high"} or flag.signal_strength in {"moderate", "strong"}
```

**Critical classification (§4.10), evaluated first, in order.** A customer is
`critical` if **any** rule holds:

| Rule | Condition | Recorded `ReasonType` |
|---|---|---|
| 1 | `cancellation_intent` AND strength ∈ {moderate, strong} | `critical_cancellation_intent` |
| 2 | `cancellation_intent` AND strength == strong AND ≥1 other significant flag of a **different** `flag_type` | `critical_cancellation_plus_significant_flag` |
| 3 | `quantitative_score >= quantitative_thresholds["high"]` AND `renewal_or_contract_concern` strength == strong | `critical_high_quant_plus_contract_concern` |
| 4 | `quantitative_score >= quantitative_thresholds["high"]` AND ≥1 flag with `severity == "high"` AND `recurrence_count >= 2` | `critical_repeated_high_severity_plus_high_quant` |

- The combined **score alone can never produce critical** (invariant, §4.27).
- Rule 1 alone is sufficient; weak-only evidence never becomes critical.
- All applicable rules **may** be recorded; **at least one must always** be
  present for every critical customer (§4.11).

**High / Medium / Low (§4.12):**

```python
if combined_score >= risk_thresholds["high"]:   level = "high"
elif combined_score >= risk_thresholds["medium"]: level = "medium"
else:                                            level = "low"
```

**Insufficient data (§4.13):**

```python
node2.model_status in {INSUFFICIENT_DATA, FAILED}
AND node3_support_data_status == "no_data"
```

Secondary "both technically exist but neither usable" cases must be represented
by explicit upstream status flags — **never inferred from a low score**. If
either node carries meaningful evidence, the customer stays in the main list.
No support data ≠ low risk; no quantitative score ≠ low risk.

### 5.5 `node4/confidence.py` — Task 5.10 (§4.14, §4.15)

```python
QUANT_CONFIDENCE_BY_STATUS = {
    "READY": 1.00, "WARNING": 0.70, "FALLBACK": 0.45,
    "INSUFFICIENT_DATA": 0.00, "FAILED": 0.00,
}
quant_confidence = QUANT_CONFIDENCE_BY_STATUS[node2.model_status]
qualitative_confidence = node3.overall_signal_confidence

combined_confidence = round(
    confidence_weights["quantitative"] * quant_confidence
    + confidence_weights["qualitative"] * qualitative_confidence,
    3,
)
combined_confidence = max(0.0, min(1.0, combined_confidence))
```

Risk level and confidence are **independent** (HIGH+LOW or LOW+HIGH allowed).

**Missing upstream node behavior (§4.14):**

- **Node 3 unavailable:** `qualitative_score = 0.0`,
  `qualitative_confidence = 0.0`; add warning
  `"Node 3 unavailable; quantitative-only synthesis used."` quantitative result
  unchanged. Absence is never positive evidence.
- **Node 2 unavailable:** `quantitative_score = None`, `quant_confidence = 0.0`;
  add warning `"Node 2 unavailable; qualitative-only synthesis used."` Strong
  qualitative evidence can still yield **High** or **Critical** via explicit
  rules.

### 5.6 `node4/reasons.py` — Tasks 5.8, 5.9, §4.16 (§4.11, §4.16)

Build `primary_reasons: list[StructuredReason]` deterministically from computed
values only. `StructuredReason = {reason_type, source ∈ {node2,node3}, severity,
evidence_ref}`.

- Every critical customer → ≥1 critical `reason_type` (the qualifying rule(s)).
- Include applicable non-critical reasons, e.g. `high_quantitative_risk`,
  `moderate_quantitative_risk`, `strong_support_signal`,
  `moderate_support_signal`, `repeated_support_issue`, `high_support_urgency`,
  `limited_support_data`, `missing_support_data`, `missing_quantitative_data`,
  `quantitative_qualitative_agreement`, `quantitative_qualitative_conflict`.
- Conflicts are **recorded, not hidden** (§4.17): high quant + positive support,
  low quant + strong cancellation, high quant + `no_data`.
- Human-readable strings are rendered deterministically from these structures —
  never by the LLM.

### 5.7 `node4/evidence.py` — Task 5.9/§4.23

```
node2: {model_version, customer_state, feature_refs}
node3: {signal_version, thread_ids, message_ids}
```

- Populate node3 `thread_ids`/`message_ids` from `AggregatedRiskFlag`
  `evidence_message_ids` / `strongest_evidence` (thread IDs from Node 3
  `thread_signals` if needed).
- **Never fabricate** evidence; if absent, represent the absence explicitly.

**Locked — D-3 `signal_version`.** `Node3Output` has no `signal_version` field
(§3.13 exposes `aggregation_version` etc.), so Node 4 derives a deterministic
**composite** from the customer's `CustomerSupportSignals.meta` in fixed field
order (never a single existing field pretending to be equivalent):

```python
def signal_version(meta) -> str:
    return (
        f"n3;pre={meta.preprocessing_version}"
        f";agg={meta.aggregation_version}"
        f";vocab={meta.vocabulary_version}"
        f";prompt={meta.prompt_version}"
        f";model={meta.model_version}"
    )
```

Node 3 remains frozen; the composite lives in `node4/evidence.py`.

### 5.8 `node4/ranking.py` — Task 5.11 (§4.18–§4.20)

Separate insufficient-data customers **before** sorting. Sort the main list
(Critical, High, Medium, Low) by this tuple, all deterministic:

1. Risk level — Critical > High > Medium > Low.
2. Combined score — descending.
3. Cancellation language detected (`churn_language_detected == True`) first.
4. Strongest qualitative signal — `strong > moderate > weak > none`.
5. Quantitative risk — descending (`None` sorts last deterministically).
6. Combined confidence — descending.
7. `customer_id` — ascending lexicographic (final determinism key).

Assign `rank = 1, 2, 3, …` after sorting. Insufficient-data accounts get
`rank = None` and are **not** ranked in the main list.

### 5.9 `node4/node.py` — Tasks 5.2, 5.12 entry point

`run_node4(node2_output, node3_output, config) -> Node4Output` implementing the
§4.18 procedure exactly:

1. Build the customer union.
2. Compute quantitative values (`quantitative.py`).
3. Compute qualitative values (`qualitative.py`).
4. Compute combined score (`scoring.py`).
5. Evaluate critical rules (`rules.py`).
6. Assign risk level.
7. Compute confidence (`confidence.py`).
8. Generate deterministic reasons (`reasons.py`) + evidence (`evidence.py`).
9. Split insufficient-data customers.
10. Sort (`ranking.py`).
11. Assign sequential ranks.

Also provide `main(argv)` as the CLI for `churn-survival node4` (mirrors
`node2.node.main` / `node3.node.main`; input/output file paths + `--config`).

**Locked — D-4 `ranked_at`.** A `datetime`; to keep the §4.27 bit-identical
guarantee it is derived deterministically from the declared reference date and
**never** from wall-clock `now()`:

```python
ranked_at = datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)
```

It is excluded from any content hash. Using `datetime.now()` would make the
determinism invariant false.

### 5.10 `node4/explain.py` — §4.25 (optional)

**Locked — D-5 (v1 scope): no LLM calls in Node 4 v1.** `explanation` is always
`None`; the deterministic result is the sole acceptance path. `explain.py` is
deferred to a later task (the contract field stays). This deliberately removes
LLM failure modes from the deterministic synthesis node. When it is implemented:

- LLM use is **optional**; absent/failure → `explanation = None` and Node 4
  continues normally (invariant: LLM failure must not block deterministic output).
- LLM receives only: computed risk level, combined score, confidence, structured
  reasons, approved evidence refs/text.
- LLM is **forbidden** from changing score/confidence/risk level/rank, creating or
  deleting evidence/reasons, or re-evaluating critical rules.
- Output may be polished text only; the deterministic output must remain complete
  and valid without any LLM involvement.

---

## 6. Failure Handling (§4.26)

Node 4 must **never silently discard customers**:

| Situation | Behavior |
|---|---|
| In Node 2, not Node 3 | quantitative-only synthesis |
| In Node 3, not Node 2 | qualitative-only synthesis (when meaningful evidence exists) |
| In neither | cannot exist in the universe |
| Whole-input schema invalid | validate to `Node2Output`/`Node3Output` at the boundary; fail **loudly** — no partial synthesis |
| **Partial alignment (D-6):** customer `scored` but no `risk_scores`/survival entry | treat quantitative as **missing** (`quantitative_score = None`, `quant_confidence = 0.0`), append an identifying error, **keep** the customer and synthesize qualitative-only |
| **Duplicate upstream record (D-7)** | append an identifying error; deterministically keep the **first occurrence in input order**; continue |
| Other malformed/inconsistent record | append an error to `processing_report` (identify the customer when possible); continue with unaffected customers |

Because inputs are validated Pydantic models, *schema* corruption cannot reach
Node 4 through the typed path — the per-customer cases above are logical /
alignment inconsistencies, not schema failures.

---

## 7. Required Invariants (Task 5.12, §4.27)

Each item below needs a dedicated test in `tests/node4/`:

1. **Determinism** — same inputs + config versions → identical output.
2. **Customer universe** — every upstream customer appears exactly once, in one of the two lists.
3. **Rank stability** — adding an unrelated customer does not change others' level/score/confidence/relative order (absolute rank numbers may shift if inserted ahead).
4. **Explanation independence** — changing/removing the explanation changes nothing else.
5. **LLM failure** — LLM unavailable does not prevent the deterministic result.
6. **Node 3 failure** — quantitative-only synthesis still works.
7. **Node 2 failure** — strong qualitative signals still produce High/Critical when rules allow.
8. **Critical rule requirement** — every critical customer has ≥1 qualifying rule in `primary_reasons`.
9. **Critical score protection** — a high combined score alone never yields critical.
10. **Positive feedback protection** — positive feedback never reduces risk.
11. **No-data protection** — `no_data` is never treated as evidence of health.
12. **Evidence traceability** — every critical customer has reconstructable evidence refs.
13. **Rank independence** — explanation text never alters ranking.

Additional invariants from spec closure:

14. **Scored-subset alignment (adversarial, D-6/D-1)** — given
    `C1 scored, C2 not_enough_data, C3 excluded, C4 scored, C5 scored`, assert
    `C1 → scored index 0`, `C4 → index 1`, `C5 → index 2`, and `C2/C3 → None`.
    This proves the second indexing system (full customer index → scored subset)
    never leaks `C2`/`C3`'s position into `C4`.
15. **Union matrix** — all four cases: `yes/yes → one account`, `yes/no →
    quant-only`, `no/yes → qual-only`, `no/no → impossible`.
16. **Duplicate upstream record (D-7)** — deterministic first-occurrence retention
    + an error recorded.
17. **Partial-alignment demotion (D-6)** — a `scored` customer with a missing
    score is retained and synthesized qualitative-only, with an error recorded.

Plus `summary_stats` must equal actual list lengths (§4.24), and malformed
upstream records produce errors without aborting the run (§4.26).

---

## 8. Versioning (§4.28)

Every Node 4 run records:

- `ranking_version`, `threshold_version`, `critical_rules_version`, `reference_date`
  (carried in `RankedAccountMeta`).
- Preserve upstream `Node 2 model_version` and `Node 3 signal_version` in
  `evidence_refs`.

Any change to scoring, thresholds, ranking, critical rules, or evidence selection
requires an appropriate version bump (never an in-place edit).

---

## 9. Integration & Test Plan

### 9.1 Pipeline wiring

- Add `"node4"` to `pipeline/main.py::IMPLEMENTED_NODES` and dispatch to
  `node4.node.main`.
- Update `tests/test_pipeline.py`: the "not implemented" assertion moves to
  `node5`; add a Node 4 dispatch assertion.

### 9.2 Test plan (`tests/node4/`)

Map one test module per module/invariant group:

- `test_config.py` — Task 5.1 weight-sum invariant; `load_node4_config("1")`.
- `test_quantitative.py` — precedence (`survival_prob_90d` > `risk_score`), `None`
  path, bounds, identity-clamp normalization (D-1), and the assertion that
  `1 - survival_prob_90d == risk_score` when 90d is `AVAILABLE`.
- `test_alignment.py` — **I-14 scored-subset adversarial alignment** (C1 scored /
  C2 not_enough_data / C3 excluded / C4 scored / C5 scored).
- `test_universe.py` — **I-15 four-case union matrix**, plus I-16 duplicate
  retention and I-17 partial-alignment demotion.
- `test_qualitative.py` — strongest-flag selection, recurrence bonus table
  (1→0.00 … 5+→+0.20), positive-feedback non-reduction, tie-breaks.
- `test_scoring.py` — weighted sum, agreement bonus, `[0,1]` clamp.
- `test_rules.py` — one positive + one negative per critical rule; weak-only never
  critical; significant predicate; High/Medium/Low thresholds; insufficient data.
- `test_confidence.py` — status→confidence map; missing-node warnings; rounding.
- `test_reasons.py` — critical always has a qualifying reason; applicable reasons.
- `test_ranking.py` — 7-key sort, sequential ranks, insufficient ranks `None`,
  rank stability.
- `test_evidence.py` — refs populated; absence represented; no fabrication.
- `test_output.py` — universe, `summary_stats` == list lengths, malformed-record
  errors.
- `test_invariants.py` — the 13 §4.27 invariants plus I-14…I-17.

**Verification bar:** full suite green; `ruff check .` clean; `mypy schemas`
clean (Node 4 modules typed pragmatically); Dataset 7 node2+node3 outputs can be
fed through Node 4 as an E2E smoke test.

---

## 10. Locked Decisions (v1)

These decisions were resolved during **spec closure** against the actual Node 2 /
Node 3 / config code (not guessed). They are part of the Node 4 v1 contract. Any
change to them requires a version bump (§4.28).

| ID | Decision |
|---|---|
| **D-1** | `normalize_risk_score(v) = clamp(round(v, 3), 0, 1)`, `normalization_version = "risk_norm_v1.0"`. Node 2's `risk_score` is already `clip(1 - S(t_ref), 0, 1)` (`node2/cox.py:103`), so this is an **identity clamp, not a second scoring system**. When 90d is `AVAILABLE`, `1 - survival_prob_90d == risk_score` (both `1 - S(90)`); assert it. When 90d is unavailable, the fallback is `1 - S(median tenure)` — a valid risk probability on a different time base; record that base. `None` stays `None`. |
| **D-2** | `top_drivers`: from `feature_associations` (only when `model_type == cox_ph`), keep `hazard_ratio > 1.0`, sort by `coefficient` descending, tie-break `feature` ascending, cap at `top_drivers_max` (= 5). KM/none → `[]`. |
| **D-3** | `signal_version` = deterministic composite of the customer's Node 3 meta: `n3;pre=<preprocessing_version>;agg=<aggregation_version>;vocab=<vocabulary_version>;prompt=<prompt_version>;model=<model_version>`. Node 3 stays frozen. |
| **D-4** | `ranked_at = datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)`. Never `datetime.now()`. Excluded from any content hash. |
| **D-5** | No LLM calls in Node 4 v1; `explanation` is always `None`; `explain.py` deferred. The deterministic result is the sole acceptance path. |
| **D-6** | Whole-input schema failure → fail loudly (no partial synthesis). Per-customer alignment mismatch (`scored` but no score) → quantitative treated as missing (`None`, `quant_confidence = 0.0`), error recorded, customer **retained** and synthesized qualitative-only. |
| **D-7** | Duplicate upstream customer record → error recorded; deterministically keep the **first occurrence in input order**. |
| **D-8** | `insufficient_data ⇔` (no quantitative value for the customer) **AND** (`node3.support_data_status == "no_data"`). Status-driven, never inferred from a low score; the `model_status ∈ {INSUFFICIENT_DATA, FAILED}` case is a subset. A `scored` low-risk customer with `no_data` remains in the main list. |
| **D-9** | Fallback normalization is necessary (to supply a value when 90d is unavailable) but non-arbitrary — it is the D-1 clamp of an already-probability quantity. |

**Consequence for `Node4Config`:** add `normalization_version` (required str) and
`top_drivers_max` (int, default 5) to `config/models.py::Node4Config` and to
`config/node4/v1.json` (see §4).

---

## 11. Traceability (Task → §4 → Artifact)

| Task | Architecture | Artifact |
|---|---|---|
| 5.1 Config object | §4.2 | `config/node4/v1.json` (model/loader exist) |
| 5.2 Customer universe | §4.3 | `node4/node.py` |
| 5.3 Quantitative normalization | §4.4 | `node4/quantitative.py` |
| 5.4 Qualitative scoring + recurrence | §4.5 | `node4/qualitative.py` |
| 5.5 Strongest-signal tie-breaks | §4.6 | `node4/qualitative.py` |
| 5.6 Combined score + agreement | §4.7 | `node4/scoring.py` |
| 5.7 Significant flag | §4.8 | `node4/rules.py` |
| 5.8 Risk level + critical rules | §4.9–§4.12 | `node4/rules.py` |
| 5.9 Critical rule recording | §4.11 | `node4/reasons.py`, `node4/evidence.py` |
| 5.10 Confidence + missing nodes | §4.14, §4.15 | `node4/confidence.py` |
| 5.11 Ranking & sort | §4.18–§4.20 | `node4/ranking.py` |
| 5.12 Invariants tests | §4.26–§4.28 | `tests/node4/` |

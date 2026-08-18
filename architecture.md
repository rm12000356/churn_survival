# Churn Survival Analysis System

**Version:** 1.2 (Implementation-Locked) · Node 3 spec added as v1.3 (Implementation-Locked) · Node 4 spec added as v1.2 (Implementation-Locked) · Node 5 spec added as v1.0 (Implementation-Locked)
**Date:** 2026-08-17
**Status:** Design locked for Node 1, Node 2, Node 3, Node 4, and Node 5. This is the single source of truth for implementation. Full 5-node pipeline is complete.

---

## 0. System Statement

The system is designed to support heterogeneous customer-data formats through adapters. It does not promise that every conceivable input will work. It promises a stable internal contract and a clear process for adding new formats.

High-level pipeline:

```
textRaw Data
  → Router
  → Adapter (deterministic preferred)
  → Canonical Schema
  → Validation
  → Feature Gate
  → Model Eligibility
  → Model Fit / Score
  → Structured Output
```

- **Node 1** owns everything up to and including the validated canonical dataset.
- **Node 2** owns Feature Gate through final model output.
- **Node 3** owns qualitative support signal extraction + evidence. Completely independent of Node 2.
- **Node 4** owns deterministic synthesis → ranked, explainable account list.
- **Node 5** owns the client-facing risk report (presentation & communication layer). The authoritative decision remains Node 4.

---

## 0.1 Router Decision Rule (Final)

The router does not use a vague "if known, else LLM" heuristic. It uses explicit signature detection.

**Mechanism:**

Every deterministic adapter declares a signature that describes the data shape it can handle with high confidence.

Examples of signature elements:
- Required column name patterns (exact or regex)
- Required sheet names (for Excel)
- Expected presence/absence of key fields
- Simple structural cues (single table vs multi-sheet, etc.)

**At runtime the router:**
1. Extracts a lightweight fingerprint from the incoming data (column names, sheet names, basic structure).
2. Asks each registered deterministic adapter whether its signature matches (via `can_handle()` or an explicit `matches_signature()` method).
3. Collects all adapters that claim a match, along with a confidence score if the adapter provides one.

**Decision:**
- If one or more deterministic adapters match with high confidence → choose the best-matching one (highest confidence, or explicit priority order).
- If no deterministic adapter matches with high confidence → fall back to the LLM mapping-report path.
- Never call the LLM if a high-confidence deterministic match exists.

**Many-adapters rule (10–20+ adapters):** the router always evaluates *every*
registered deterministic adapter — default built-ins plus every confirmed mapping
config — against the incoming fingerprint, then picks the best match
deterministically. A confirmed mapping config is `confidence=1.0, priority=0`, so
for its exact fingerprint it always wins; every adapter that matched is recorded
in the routing decision and surfaced in the validation report
(`matched_candidates`). Two confirmed configs with the same fingerprint are a loud
configuration error, never a silent override.

This keeps the common path fast, cheap, and fully deterministic, while still allowing the system to handle genuinely new shapes safely.

---

## 1. Node 1 — Canonical Schema + Adapter Pattern

### 1.1 Design Goals

- One fixed internal data shape that the rest of the system can trust completely.
- External formats may vary; the inside stays stable.
- Deterministic adapters are the preferred and long-term path.
- LLM is used only as a one-time translator that produces a reviewable mapping report. The confirmed report becomes a deterministic configuration.
- New fields can always be stored. They are never automatically used for modeling.
- Full auditability and reproducibility of every record.

### 1.2 Node 1 Output Contract (Explicit Interface)

Node 1 must return exactly this structure:

```python
{
    "canonical_dataset": list[dict],  # Only fully validated records
    "validation_report": {
        "status": "PASSED" | "FAILED" | "PARTIAL",
        "n_input_rows": int,
        "n_accepted": int,
        "n_rejected": int,
        "errors": list[dict],  # Detailed per-record or per-column errors
        "warnings": list[str],
        "adapter_used": str,
        "matched_candidates": list[str],  # every adapter that matched; first is the winner
        "mapping_version": str,
        "reference_date": str,
    },
}
```

If validation fails completely, `canonical_dataset` is empty and the pipeline stops for that batch.

### 1.3 Formal Observation Model (Canonical Schema)

Survival analysis requires an explicit observation window. The canonical record is therefore:

```python
{
    "customer_id": str,  # Required, unique within dataset
    "observation_start": str,  # ISO-8601 date – origin of observation
    "observation_end": str,  # ISO-8601 date – churn date or censoring date
    "event_observed": int,  # 0 = right-censored (still active), 1 = churned
    "tenure": float,  # Derived: days between observation_start and observation_end
    "core_features": {  # Only pre-approved keys allowed
        # Example keys (exact set is deployment-specific and gated):
        "plan_tier": str,
        "contract_length_months": float,
        "usage_frequency": float,
    },
    "extra_features": {  # Open dictionary – storage only
        # Any additional fields land here
    },
    "meta": {
        "source_adapter": str,
        "mapping_version": str,
        "ingested_at": str,  # ISO-8601
        "original_row_id": str | None,
        "reference_date": str,  # Dataset-level cut-off date (critical for reproducibility)
    },
}
```

#### Field Rules (Non-Negotiable)

| Field | Type | Constraints & Rules |
|---|---|---|
| `customer_id` | str | Required, unique, non-empty |
| `observation_start` | str | Required, valid ISO date, ≤ observation_end |
| `observation_end` | str | Required, valid ISO date. For active customers = dataset reference_date. For churned customers = actual churn date. |
| `event_observed` | int | Exactly 0 or 1 |
| `tenure` | float | Derived, ≥ 0, finite. Must equal `(observation_end - observation_start).days` |
| `core_features` | dict | Only keys that have been explicitly approved for modeling |
| `extra_features` | dict | Any keys allowed. Never automatically fed to the model |
| `meta.reference_date` | str | Single cut-off date declared for the entire dataset. Used for all tenure calculations of active customers |

**Core-feature whitelist is deployment-union:**

`CoreFeatures` declares the union of all known deployment core vocabularies
(e.g. SaaS: `plan_tier`, `contract_length_months`, `usage_frequency`; telecom:
`contract`, `internet_service`, `monthly_charges`, `senior_citizen`). Every field
is schema-optional because the *deployment-specific* required set and types are
gated by `Node1Config.approved_core_keys` (§1.7 Gate 8) — unknown keys are always
rejected by `extra="forbid"` regardless of deployment. A deployment whose raw
data cannot populate any approved core feature ends in an explicit
`COLUMN_MISSINGNESS` batch rejection, never a fabricated PASSED.

#### Reproducibility rule (critical)

Tenure for active customers is always calculated against the dataset's declared `reference_date`. Rerunning the identical raw file on a later calendar day must produce identical tenure values.

#### Example — Active Customer

```json
{
  "customer_id": "cus_8f3a2b1c",
  "observation_start": "2025-03-12",
  "observation_end": "2026-08-15",
  "event_observed": 0,
  "tenure": 521.0,
  "core_features": {
    "plan_tier": "pro",
    "contract_length_months": 12.0,
    "usage_frequency": 28.4
  },
  "extra_features": {
    "last_login_days_ago": 3,
    "support_tickets_90d": 1,
    "region": "EU",
    "sales_rep": "alice@company.com"
  },
  "meta": {
    "source_adapter": "hubspot_messy_v3",
    "mapping_version": "map_2026-08-12T14:22:00Z",
    "ingested_at": "2026-08-17T10:05:33Z",
    "original_row_id": "row_1842",
    "reference_date": "2026-08-15"
  }
}
```

#### Example — Churned Customer

```json
{
  "customer_id": "cus_9d2e4f7a",
  "observation_start": "2024-11-03",
  "observation_end": "2026-02-18",
  "event_observed": 1,
  "tenure": 472.0,
  "core_features": {
    "plan_tier": "basic",
    "contract_length_months": 1.0,
    "usage_frequency": 4.1
  },
  "extra_features": {
    "cancellation_reason": "price"
  },
  "meta": {
    "source_adapter": "hubspot_messy_v3",
    "mapping_version": "map_2026-08-12T14:22:00Z",
    "ingested_at": "2026-08-17T10:05:33Z",
    "original_row_id": "row_991",
    "reference_date": "2026-08-15"
  }
}
```

### 1.4 Adapter Interface Contract

```python
class Adapter(Protocol):
    name: str
    version: str

    def can_handle(self, sample: Any) -> bool:
        """Return True only if this adapter is confident it can process the data."""

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        """
        Transform raw data into a list of canonical records.
        Must calculate tenure and set observation_end correctly.
        Must raise clear exceptions on failure.
        Must never emit non-canonical top-level fields.
        """

    def get_mapping_config(self) -> dict:
        """Return the exact mapping configuration that was used (for audit)."""
```

**Rules:**

- Deterministic adapters are preferred for any format that has been seen and confirmed.
- The adapter receives the dataset `reference_date` and is responsible for correct tenure calculation.
- The adapter must never introduce temporary fields such as `tenure_start_date` into the canonical output.

### 1.5 Deterministic Adapters (Preferred Path)

Write pure Python parsers for known shapes. Examples that should have deterministic adapters:

- Clean single-sheet CSV with obvious column names
- Multi-sheet Excel with known structure
- Stripe-style customer + subscription export
- HubSpot-style CRM export
- Zendesk / Intercom style dumps

Once a format has been successfully mapped and confirmed, it must become a deterministic adapter. The LLM path is only for the first encounter.

Confirmed mapping configs live in `config/mappings/map_*.json` and are loaded as
deterministic adapters at routing time. The loader only ever reads `map_*.json` —
draft reports live separately in `config/mappings/drafts/` and are never
interpreted as adapters. Duplicate fingerprints across confirmed configs fail
loudly.

### 1.6 LLM-Assisted Path (Unknown / Messy Formats)

When the router cannot match a deterministic adapter:

1. LLM receives a sample (headers + 10–30 rows + sheet names if applicable).
2. LLM produces a structured **Mapping Report** (never direct transformation).
3. Human reviews and confirms or corrects the report.
4. Confirmed report is stored as a deterministic mapping configuration.
5. Future files that match the fingerprint use the deterministic path.

#### Exact Mapping Report Schema

```json
{
  "source_fingerprint": {
    "headers_hash": "sha256 of sorted header names",
    "sheet_names": ["Customers", "Subscriptions"],
    "column_names": ["Cust ID", "Start Date", "Status", ...],
    "sample_dtypes": {"Cust ID": "object", "Start Date": "object", ...},
    "n_sample_rows": 25
  },
  "proposed_mappings": [
    {
      "source_column": "Cust ID / Account Number",
      "target_field": "customer_id",
      "confidence": 0.97,
      "transformation": "str.strip()",
      "notes": "Appears unique"
    },
    {
      "source_column": "Start Date",
      "target_field": "observation_start",
      "confidence": 0.93,
      "transformation": "parse_date(mixed_formats=True)",
      "notes": "Mixed formats observed (YYYY-MM-DD and MM/DD/YYYY)"
    },
    {
      "source_column": "Churn Date",
      "target_field": "observation_end",
      "confidence": 0.89,
      "transformation": "parse_date; if null/empty then use reference_date",
      "notes": "Null means still active → censor at reference_date"
    },
    {
      "source_column": "Status",
      "target_field": "event_observed",
      "confidence": 0.91,
      "transformation": "map({'Churned': 1, 'Active': 0, 'Yes': 1, 'No': 0, True: 1, False: 0})"
    }
  ],
  "unmapped_columns": ["Internal Notes", "Legacy Flag"],
  "suggested_extra_features": [
    {"source": "Plan Name", "suggested_key": "plan_name"},
    {"source": "Last Login", "suggested_key": "last_login_days_ago"}
  ],
  "data_quality_flags": [
    "Column 'Start Date' has 12% unparseable values",
    "3 duplicate customer_ids found in sample"
  ],
  "recommended_action": "create_deterministic_adapter",
  "llm_model_used": "...",
  "generated_at": "2026-08-17T09:41:12Z"
}
```

**Audited transformation subset:**

`transformation` strings are parsed by a fixed, audited set — no arbitrary code
execution. Supported ops: `str.strip()`, `to_float`, `to_int`, `parse_date`,
`map({...})` (literal dict, case-insensitive key match), `months_before(reference_date)`,
`snapshot_end(reference_date)`, and `row_number` (deterministic 0-based row index
used only for `customer_id` when the source has no ID column — recorded as a
declared assumption in the mapping notes).

`months_before(reference_date)` is the documented way to ingest point-in-time
snapshot data (e.g. public churn datasets that report tenure in months but carry
no signup/churn dates). It derives a date from a numeric value `v` as
`reference_date - round(v * 30.4375)` days. Because the snapshot does not record
the actual churn date, the churned customer's observation window must also be
closed deterministically: `snapshot_end(reference_date)` returns the declared
cut-off (the input value is ignored), so the window collapses to `reference_date`
and `event_observed` alone distinguishes churn. This is a declared, auditable
assumption recorded in the mapping notes, never a hidden inference; the LLM has
no authority to introduce it, only the human-confirmed mapping config does.

**Fingerprint rule:**

The fingerprint must include headers, sheet names, column set, and basic schema signature. Sample statistics alone are insufficient. Human confirmation remains the final safety gate.

**Guided onboarding workflow (CLI, §1.6):**

Per-company adapters/configs are the expected operating mode — different companies
have their own rows, models, and forms. The generic routing machinery stays; the
CLI guides the user to create the two artifacts:

1. `churn-survival node1 <file>` refuses an unknown shape with the fingerprint and
   a numbered onboarding checklist.
2. `churn-survival map <file>` writes `config/mappings/drafts/draft_<hash12>.json`
   — a `MappingReport` skeleton with the real fingerprint pre-filled, every column
   listed as unmapped, `llm_model_used="manual/template"` (no LLM required).
   `--llm` generates a proposal when an LLM provider is configured.
3. The user fills in `proposed_mappings` / `suggested_extra_features`, then
   `churn-survival map <draft.json> --confirm` validates and persists
   `config/mappings/map_<timestamp>.json` (deterministic adapter, `confidence=1.0,
   priority=0`).
4. The user copies `config/node1/_template.json` to `config/node1/v<company>.json`
   and sets `approved_core_keys` + `core_key_types` for their deployment.
5. A brand-new core feature requires a deliberate one-line addition to
   `CoreFeatures` (§1.3 union whitelist). A new audited transform op (e.g.
   `row_number`) requires an `adapters/mapping_adapter.py` extension recorded here.
6. Re-run: `churn-survival node1 <file> --config <company>`. Full walkthrough in
   `docs/onboarding.md`.

Deployment configs gate core *types* per company: `Node1Config.core_key_types`
(§1.7 Gate 8) declares each approved key's type (`string`|`float`|`int`); undeclared
keys default to `string`.

### 1.7 Validation Stage (Hard Gate)

Executed immediately after the adapter. Any failure stops or quarantines the affected records.

**Mandatory checks:**

- All required top-level keys present.
- `customer_id` non-empty and unique within the batch.
- `observation_start` and `observation_end` are valid ISO dates.
- `observation_start` ≤ `observation_end`.
- `tenure` ≥ 0, finite, and exactly matches the date difference in days.
- `event_observed` is exactly 0 or 1.
- No future leakage: `observation_end` ≤ `reference_date` for all records.
- `core_features` contain only approved keys and correct types.
- Missingness in any core feature below configured threshold (default reject if > 30%).
- No infinite or NaN values in numeric fields.
- Basic statistical sanity (tenure distribution not dominated by zeros or extreme outliers).

**Output:** Clean list of canonical records + detailed validation report, or explicit failure.

### 1.8 Feature Gate (Boundary Between Node 1 and Node 2)

- Every new field lands in `extra_features` and is stored permanently.
- Promotion to `core_features` is a separate, explicit decision.
- Promotion requires sufficient non-missing data, meaningful variation, and either statistical association with the event or explicit domain approval.
- With sparse events the default posture is reject promotion.
- Multicollinearity signals are generated here but treated as warnings, not automatic killers.

---

## 2. Node 2 — Survival Model

### 2.1 Design Goals

- Produce individual customer risk estimates using survival analysis when the data supports it.
- Fall back honestly when the data does not support a reliable model.
- Never invent confidence.
- Maintain one downstream contract regardless of model type.
- Full reproducibility via versioned model artifacts.
- Clear separation between model fitting and scoring.

### 2.2 Separation of Fitting and Scoring

```python
def fit_model(canonical_dataset: list[dict], config: dict) -> ModelArtifact:
    """Train and return a fully versioned model artifact."""


def score_customers(model_artifact: ModelArtifact, customers: list[dict]) -> dict:
    """Score new or historical customers using a previously fitted model."""
```

This separation is mandatory for production use (scheduled retraining + exact historical rescoring).

### 2.3 Model Status Values

| Status | Meaning | risk_scores emitted? |
|---|---|---|
| READY | Model is trustworthy | Yes |
| WARNING | Model fitted but has limitations | Yes (clearly caveated) |
| FALLBACK | Simpler method used (Kaplan-Meier) | null |
| INSUFFICIENT_DATA | Data cannot support any useful model | null |
| FAILED | Technical failure to fit | null |

### 2.4 Model Eligibility (Separate Component Before Fitting)

**Hard gates (any failure → CoxPH is not allowed):**

- Required survival columns present and correctly typed
- Valid observation window and tenure
- `event_observed` strictly binary
- Minimum number of customers
- Minimum number of events relative to number of predictors
- Every selected predictor has meaningful variation
- No catastrophic missing-data problem after encoding
- Events-to-predictors relationship acceptable

**Warning signals (fit proceeds, status becomes WARNING):**

- Low absolute event count
- Elevated missingness
- Strong pairwise correlations among predictors
- Short overall follow-up
- Highly uneven event distribution over time

Eligibility is evaluated before any model is fitted.

### 2.5 Multicollinearity Handling

- Pairwise correlations always checked.
- VIF only when the number of predictors makes it meaningful.
- Treated as a warning / investigation signal, not an automatic feature-killing rule.
- With only 2–3 features, domain interpretability and data quality take priority.
- Final keep/drop decision remains with the Feature Gate.

### 2.6 Cox Proportional Hazards Path

- Library: `lifelines.CoxPHFitter`
- Mandatory `penalizer > 0` (L2 regularization)
- Only approved `core_features` are used as predictors
- Explicit handling of tied event times
- After fitting:
  - Proportional hazards diagnostics (Schoenfeld residuals or equivalent)
  - Validation metrics (C-index preferred; bootstrap validation preferred over naïve train/test split because of small event counts)

**Proportional Hazards Violation Severity:**

- Minor / moderate → keep CoxPH, set status WARNING
- Manageable → attempt adjustment (e.g. stratification), then refit
- Serious → fall back or suppress individual scores

Statistical test results are evidence, not an automatic kill switch.

### 2.7 Survival Probability Horizons

- Default supported horizons: 30, 90, 180 days.
- Horizons are configurable (a B2B client may care about 365 days).
- A horizon is only marked `AVAILABLE` when:
  - Enough customers have been observed for at least that many days
  - Enough events exist around/after that horizon
  - The survival estimate is not dominated by extreme uncertainty

**Example fragment:**

```json
"survival_probabilities": {
  "30d": {
    "status": "AVAILABLE",
    "values": [0.97, 0.81, 0.93, ...],
    "confidence_intervals": [[0.94, 0.99], ...]
  },
  "90d": {
    "status": "AVAILABLE",
    "values": [0.89, 0.62, 0.85, ...]
  },
  "180d": {
    "status": "INSUFFICIENT_DATA"
  }
}
```

### 2.8 Fallback Path (Kaplan-Meier)

Triggered when eligibility fails or serious problems are detected after fitting.

- `risk_scores` is always null (Kaplan-Meier does not produce individual Cox-style risk scores).
- Global survival curve is always produced.
- Segment curves are produced only when a pre-approved categorical feature exists and every segment meets minimum customer count + minimum event count.
- Never create tiny, noisy segments.
- `model_type = "kaplan_meier"`, `model_status = "FALLBACK"`.

### 2.9 Cold-Start / Insufficient Observation Customers

Customers with near-zero tenure and no usable behavioral features receive an explicit `"not_enough_data"` state. They are not forced into Low/Medium/High risk tiers.

### 2.10 Deterministic Interpretation Layer

Statistical results are converted to text deterministically:

```text
coefficient = 0.42
hazard_ratio = exp(0.42) ≈ 1.52
→ "Customers in this category had an estimated 52% higher instantaneous churn hazard than the reference category, holding other variables in the model constant."
```

An LLM may later polish tone; it must never invent the statistical meaning.

### 2.11 Model Artifact (Full Versioning)

Every fitted model stores:

```json
{
  "model_version": "uuid or semantic version",
  "training_timestamp": "ISO-8601",
  "training_dataset_version": "...",
  "reference_date": "...",
  "selected_features": ["plan_tier", "contract_length_months", ...],
  "coefficients": {...},
  "baseline": {...},
  "penalizer": 0.1,
  "n_customers": 1842,
  "n_events": 23,
  "encoding_scheme": {...},
  "validation_metrics": {...},
  "assumption_check_results": {...},
  "horizon_config": [30, 90, 180]
}
```

This enables exact reproduction of any historical score.

### 2.12 Final Output Contract (Identical for All Paths)

```json
{
  "model_type": "cox_ph" | "kaplan_meier" | "none",
  "model_status": "READY" | "WARNING" | "FALLBACK" | "INSUFFICIENT_DATA" | "FAILED",
  "model_version": "string",
  "risk_scores": [float, ...] | null,
  "survival_probabilities": {
    "30d": {"status": "AVAILABLE"|"INSUFFICIENT_DATA", "values": [...], "ci": [...]},
    "90d": {...},
    "180d": {...}
  },
  "feature_associations": [                   // only when model_type == "cox_ph"
    {
      "feature": "plan_tier_pro",
      "coefficient": 0.42,
      "hazard_ratio": 1.52,
      "ci_lower": 1.11,
      "ci_upper": 2.08,
      "p_value": 0.03,
      "interpretation": "Customers in this category had an estimated 52% higher instantaneous churn hazard than the reference category, holding other variables in the model constant."
    }
  ],
  "validation_metrics": {...},
  "assumption_checks": {...},
  "warnings": ["human-readable warning strings"],
  "customer_states": ["scored" | "not_enough_data" | "excluded"]
}
```

### 2.13 Internal Flow (Node 2)

```
Validated Canonical Dataset (from Node 1)
      ↓
Feature Gate (soft multicollinearity signals)
      ↓
Model Eligibility (hard gates + warning signals)
      │
      ├── Not eligible → Fallback (Kaplan-Meier)
      │                    ↓
      │                 Output (model_type="kaplan_meier", status="FALLBACK", risk_scores=null)
      │
      └── Eligible
             ↓
           fit_model() → CoxPH (with penalizer)
             ↓
        Assumption Checks + Validation
             │
        ┌────┴────┐
        ↓         ↓
      Acceptable  Serious problems
        ↓         ↓
      Output      Fallback / Suppress scores
```

---

## 3. Node 3 — Support Signal Extraction

**Version:** 1.3 (Final – Implementation-Locked)
**Date:** 2026-08-17
**Status:** Locked. Ready for implementation.
This is the single authoritative specification for Node 3. Every decision, schema, rule, formula, derivation, edge case, and process is fully defined.

### 3.0 Purpose & Position in the Pipeline

Node 3 extracts structured qualitative signals from support interactions and preserves the underlying evidence.

It answers only:
- What evidence exists?
- How strong is that evidence?
- How confident are we in the extraction and the quality of the evidence base?

It does not decide final customer risk, does not combine signals with the survival model, and does not write client-facing narrative.

**Pipeline position:**
```
Node 1 → Canonical customer data
Node 2 → Quantitative risk scores + feature associations
Node 3 → Qualitative support signals + evidence
Node 4 → Synthesis (ranked account list)
Node 5 → Client-facing report
```

Node 3 is completely independent of Node 2.

### 3.1 Core Architecture

```
Support Data
      ↓
Deterministic Preprocessing
  (cleaning, language detection, cross-channel deduplication)
      ↓
LLM Extraction (thread level only)
      ↓
ThreadSignals                          ← permanent evidence / audit layer
      ↓
Deterministic Aggregation
  (recency-adjusted + recurrence-aware)
      ↓
CustomerSupportSignals                 ← primary output for Node 4
```

Both layers are persisted.

### 3.2 Inputs

```python
{
    "customers": list[str],
    "support_data": list[SupportThread] | None,
    "config": {
        "lookback_days": int,
        "max_threads_per_customer": int,
        "max_messages_per_thread": int,
        "max_tokens_per_customer": int,
        "reference_date": str,
        "supported_languages": list[str],
        "aggregation_version": str,
        "vocabulary_version": str,
        "preprocessing_version": str,
        "prompt_version": str,
    },
}
```

**SupportThread**

```python
{
    "thread_id": str,
    "customer_id": str,
    "created_at": str,
    "closed_at": str | None,
    "channel": str | None,
    "subject": str | None,
    "status": str | None,
    "tags": list[str] | None,
    "messages": [
        {"message_id": str, "timestamp": str, "role": "customer" | "agent" | "system", "text": str}
    ],
}
```

### 3.3 Deterministic Preprocessing

All steps are deterministic and versioned.

- Filter to lookback window.
- Remove pure system/automated messages.
- Separate customer vs agent messages.
- Exact and near-exact message deduplication.
- **Cross-channel near-duplicate detection** (parameters in `preprocessing_version`):
  - Candidates: same `customer_id` + `created_at` within ±48 hours.
  - Similarity: cosine similarity on TF-IDF of (subject + first customer message), threshold = 0.82.
  - Additional requirement: normalized subject similarity ≥ 0.75 or shared key issue phrases.
  - Keep the thread with higher total customer-message token count.
  - Set `duplicate_of` on the collapsed thread.
  - Collapsed threads are excluded from all subsequent counts (`n_threads_in_window`, `n_messages_in_window`, `recurrence_count`).
- Deterministic language detection.
- Mark unsupported languages.
- Truncate to hard limits.
- Preserve all original `message_id`s and timestamps.

Customer-authored text is primary.

### 3.4 Thread-Level Schema (ThreadSignals)

```python
{
    "thread_id": str,
    "customer_id": str,
    "created_at": str,
    "language": str | None,
    "language_status": "supported" | "unsupported" | "unknown",
    "duplicate_of": str | None,
    "sentiment": {
        "label": "positive" | "neutral" | "negative" | "mixed" | "unknown",
        "score": float | None,
        "confidence": float,
    },
    "risk_flags": list[RiskFlag],
    "churn_language_detected": bool,
    "urgency_level": "low" | "medium" | "high" | "unknown",
    "key_themes": list[str],
    "meta": {
        "n_customer_messages": int,
        "n_agent_messages": int,
        "n_tokens_sent": int,
        "processed_at": str,
        "prompt_version": str,
        "model_version": str,
    },
}
```

**RiskFlag**

```python
{
    "flag_type": str,
    "severity": "low" | "medium" | "high",
    "signal_strength": "weak" | "moderate" | "strong",
    "confidence": float,
    "evidence": {"message_id": str, "text": str, "timestamp": str},
    "evidence_message_ids": list[str],
}
```

#### Controlled Vocabulary (Versioned)

| flag_type | Hierarchy Rank |
|---|---|
| cancellation_intent | 1 |
| renewal_or_contract_concern | 2 |
| product_bug_or_outage | 3 |
| poor_support_experience | 3 |
| billing_complaint | 4 |
| feature_missing | 4 |
| usage_drop_related | 4 |
| competitor_mention | 5 |
| positive_feedback | — |
| other | residual |

**Vocabulary Governance:**
`other` is reviewed at least every 4 weeks or when it exceeds 20% of flags. Frequent patterns are promoted (new `vocabulary_version`) or confirmed residual. High-priority flags may never be silently moved into `other`.

### 3.5 Customer-Level Schema (CustomerSupportSignals)

```python
{
    "customer_id": str,
    "support_data_status": "no_data" | "limited_data" | "sufficient_data",
    "has_support_data": bool,
    "n_threads_in_window": int,
    "n_messages_in_window": int,
    "latest_interaction_at": str | None,
    "overall_sentiment": {
        "label": "positive" | "neutral" | "negative" | "mixed" | "unknown",
        "score": float | None,
        "confidence": float,
    },
    "risk_flags": list[AggregatedRiskFlag],
    "signal_strength": "none" | "weak" | "moderate" | "strong",
    "overall_signal_confidence": float,
    "key_themes": list[str],
    "urgency_level": "low" | "medium" | "high" | "unknown",
    "escalation_signal": bool,
    "churn_language_detected": bool,
    "summary": str | None,
    "meta": {
        "lookback_days": int,
        "processed_at": str,
        "prompt_version": str,
        "model_version": str,
        "aggregation_version": str,
        "vocabulary_version": str,
        "preprocessing_version": str,
    },
}
```

**AggregatedRiskFlag**

```python
{
    "flag_type": str,
    "severity": "low" | "medium" | "high",
    "signal_strength": "weak" | "moderate" | "strong",
    "confidence": float,
    "recurrence_count": int,
    "first_observed_at": str,
    "last_observed_at": str,
    "strongest_evidence": {"message_id": str, "text": str, "timestamp": str},
    "evidence_message_ids": list[str],
}
```

### 3.6 Signal Hierarchy & Selection Rule

Hierarchy ranks as defined in the vocabulary table.

**Explicit ordering rule when selecting overall signal strength and primary flags:**
- Higher hierarchy rank always wins.
- When hierarchy rank is equal, higher recency-adjusted strength wins.
- Tied ranks are broken by adjusted strength.

### 3.7 Numeric Strength Mapping (Locked)

```python
STRENGTH_SCORE = {"weak": 1, "moderate": 2, "strong": 3}
```

### 3.8 Aggregation Rules

#### 8.1 Recency Weighting

```python
adjusted_strength = STRENGTH_SCORE[signal_strength] * math.exp(-λ * age_days)
```

v1 defaults:
- `λ_default = 0.015`
- `λ_persistent = 0.004` (used for `cancellation_intent` and `renewal_or_contract_concern`)

#### 8.2 Flag Aggregation

For each `flag_type` (non-collapsed threads only):
- Compute recency-adjusted strength.
- Select the strongest instance according to the hierarchy + strength rule.
- Record `recurrence_count`, temporal span, and all evidence IDs.
- `AggregatedRiskFlag.confidence` = confidence of the selected strongest instance.

#### 8.3 Customer-Level Field Derivations

| Field | Rule |
|---|---|
| `overall_sentiment` | Recency-weighted average of thread sentiment scores (same λ). Label derived from weighted score via fixed thresholds. No usable threads → "unknown". |
| `urgency_level` | Maximum urgency across non-collapsed threads. |
| `escalation_signal` | True if any thread has `urgency_level == "high"` or any flag has `severity == "high"` and `recurrence_count ≥ 2`. |
| `churn_language_detected` | Logical OR across all non-collapsed threads. |
| `signal_strength` | Derived from strongest flags using hierarchy-first rule. |
| `key_themes` | Informational only. Never used as input to Node 4 logic. |

#### 8.4 evidence_quality_score

```python
def compute_evidence_quality_score(flags: list[RiskFlag]) -> float:
    if not flags:
        return 0.0
    scores = []
    for flag in flags:
        text = flag["evidence"]["text"]
        length_score = min(1.0, len(text.split()) / 12)
        clarity_bonus = (
            0.25
            if any(
                w in text.lower()
                for w in ["cancel", "cancelling", "terminate", "switch", "leave", "refund"]
            )
            else 0.0
        )
        scores.append(min(1.0, 0.75 * length_score + clarity_bonus))
    return round(sum(scores) / len(scores), 3)
```

#### 8.5 overall_signal_confidence

```python
def compute_overall_signal_confidence(
    support_data_status: str,
    n_customer_messages: int,
    n_usable_threads: int,
    schema_validity_rate: float,
    supported_language_coverage: float,
    evidence_quality_score: float,
) -> float:
    if support_data_status == "no_data":
        return 0.0

    volume_score = min(1.0, (n_customer_messages / 8) ** 0.5)
    thread_score = min(1.0, n_usable_threads / 3)

    confidence = (
        0.20 * volume_score
        + 0.20 * thread_score
        + 0.20 * schema_validity_rate
        + 0.15 * supported_language_coverage
        + 0.25 * evidence_quality_score
    )
    return round(min(1.0, max(0.0, confidence)), 3)
```

#### 8.6 support_data_status

| Status | Definition |
|---|---|
| `no_data` | Zero threads in window |
| `limited_data` | Very few customer messages or mostly low-information contents |
| `sufficient_data` | Enough customer-authored content for meaningful extraction |

### 3.9 LLM Usage Rules

- LLM used only at thread level.
- Forced structured output + Pydantic validation.
- Temperature ≤ 0.2.
- Hard per-customer token/message budget.
- No customer-level analytical LLM calls.
- `summary` must be template-generated or null. No LLM generation of summary inside Node 3.

### 3.10 Evaluation & Monitoring (Mandatory)

**Golden set (before production):**
- 150–300 real threads, double-annotated.
- Acceptance bars:
  - Cohen's κ ≥ 0.70 on `flag_type`
  - Cohen's κ ≥ 0.65 on `signal_strength`
  - LLM vs golden-set exact-match ≥ 0.75 on `cancellation_intent` and `renewal_or_contract_concern`

**Ongoing:**
- Regular human spot-checks (30–50 threads).
- Monitor flag distribution and size of `other` bucket.
- Re-evaluate golden set on every major prompt/model change.

### 3.11 Full Output Contract

```python
{
    "customer_signals": list[CustomerSupportSignals],
    "thread_signals": list[ThreadSignals],
    "processing_report": {
        "n_customers_requested": int,
        "n_customers_with_data": int,
        "n_customers_with_signals": int,
        "n_threads_processed": int,
        "n_threads_failed": int,
        "n_cross_channel_duplicates_collapsed": int,
        "llm_calls": int,
        "warnings": list[str],
        "errors": list[dict],
    },
}
```

### 3.12 Failure Modes

| Situation | Handling |
|---|---|
| Zero threads | `support_data_status = "no_data"`, confidence = 0.0, strength = "none", summary = null |
| Only agent/system messages | Limited or no usable signal |
| Invalid LLM output | Retry once → quarantine |
| Unsupported language | Mark and skip/reduce confidence |
| Cross-channel near-duplicate | Collapse under rules above, set `duplicate_of`, exclude from counts |
| Missing `customer_id` | Drop + error |

### 3.13 Versioning Requirements

Every run records:
- `prompt_version`
- `model_version`
- `aggregation_version`
- `vocabulary_version`
- `preprocessing_version`
- `lookback_days`
- `reference_date`

### 3.14 What Node 3 Does Not Do

- Final risk ranking
- Combining with Node 2 outputs
- Deciding high/medium/low risk
- Writing client-facing narrative
- Treating sentiment as equivalent to cancellation intent
- Customer-level analytical LLM calls
- Using `key_themes` as analytical input

---

## 4. Node 4 — Synthesis (Ranked Account List)

**Version:** 1.2 (Final, Implementation-Locked)
**Date:** 2026-08-17
**Status:** Locked. Ready for implementation.
This is the single authoritative specification for Node 4. All final corrections have been incorporated. No implementation decision may be inferred from undocumented behavior. All scoring, thresholding, classification, ranking, evidence handling, and failure behavior are defined below.

### 4.0 Purpose & Position

Node 4 deterministically merges quantitative survival risk from Node 2 and qualitative support signals from Node 3 into a ranked, explainable list of accounts.

It produces structured decision-support output only. It does not write the final client-facing narrative. That is the responsibility of Node 5.

**Pipeline position:**
```
Node 1 → Data trust
Node 2 → Statistical risk
Node 3 → Qualitative evidence
Node 4 → Deterministic synthesis          ← this node
Node 5 → Client communication
```

Node 4 does not modify, reinterpret, or overwrite the underlying outputs from Node 2 or Node 3. It consumes them and applies the deterministic rules defined in this document.

### 4.1 Core Principles (Non-Negotiable)

- All ranking and risk-level decisions are deterministic and driven by versioned configuration.
- LLM may only polish already-computed explanations. It has zero authority over score, rank, risk level, confidence, or evidence.
- Risk level and confidence are separate concepts.
- Strong explicit cancellation evidence cannot be overridden by a low quantitative score.
- Node 3 `no_data` is neither positive nor negative evidence.
- Full audit trail must exist from the final rank back to the relevant Node 2 model outputs, Node 3 flags, thread IDs, and message IDs.
- Customer universe is the union of all `customer_id` values appearing in Node 2 or Node 3.
- Every customer in the union must be represented in the Node 4 output.
- Same inputs plus the same configuration versions must produce bit-identical output.
- Positive support feedback must never mathematically reduce quantitative or qualitative risk.
- The combined numeric score alone can never create a critical classification.
- Every critical customer must have a recorded qualifying critical rule in `primary_reasons`.
- Node 4 must remain fully functional without the optional LLM explanation step.

### 4.2 Configuration Object (Fully Versioned)

```json
{
    "ranking_version": "1.0",
    "threshold_version": "1.0",
    "critical_rules_version": "1.0",

    "quantitative_weight": 0.60,
    "qualitative_weight": 0.40,
    "agreement_bonus": 0.05,

    "risk_thresholds": {
        "medium": 0.40,
        "high": 0.70
    },

    "quantitative_thresholds": {
        "low": 0.20,
        "medium": 0.40,
        "high": 0.70
    },

    "confidence_weights": {
        "quantitative": 0.55,
        "qualitative": 0.45
    },

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

    "strength_scores": {
        "weak": 0.33,
        "moderate": 0.66,
        "strong": 1.00
    },

    "strength_order": {
        "none": 0,
        "weak": 1,
        "moderate": 2,
        "strong": 3
    },

    "reference_date": "..."
}
```

All decision logic is driven exclusively by this configuration and the explicitly defined rules in this document.

The `positive_feedback` weight is 0.00 because positive feedback is contextual only and must never reduce risk.

### 4.3 Inputs

Node 4 receives:

```python
{"node2_output": dict, "node3_output": dict, "config": dict}
```

Where:
- `node2_output` is the complete output contract of Node 2.
- `node3_output` is the complete output contract of Node 3.
- `config` is the complete Node 4 configuration object.

The join key is:

```
customer_id
```

The customer universe is:

```
customer_ids = set(node2_customer_ids) | set(node3_customer_ids)
```

Every customer in this union must appear either in `ranked_accounts` or `insufficient_data_accounts`.

### 4.4 Quantitative Risk Normalization

Node 4 converts the Node 2 quantitative result into a normalized risk value in the range [0.0, 1.0].

```python
def quantitative_risk_score(
    survival_prob_90d: float | None,
    risk_score: float | None,
) -> float | None:

    if survival_prob_90d is not None:
        return round(1.0 - survival_prob_90d, 3)

    if risk_score is not None:
        return normalize_risk_score(risk_score)

    return None
```

**Rules:**
- `survival_prob_90d` is preferred when available.
- When `survival_prob_90d` is unavailable, Node 2's `risk_score` may be used.
- The normalization of a raw `risk_score` must be deterministic and versioned.
- If neither value is available, `normalized_risk = None`.
- A missing quantitative score must never be silently converted into evidence of low risk.

The resulting value must satisfy:

```
0.0 <= normalized_risk <= 1.0
```

where higher values indicate greater quantitative risk.

### 4.5 Qualitative Signal Scoring

Node 4 consumes the already aggregated customer-level signals from Node 3.

Node 4 does not reapply Node 3's exponential recency decay.

If Node 3 reports `support_data_status = "no_data"` or there are no qualitative risk flags:

```
qualitative_score = 0.0
```

Otherwise:

```
qualitative_score = 0.0

for flag in customer.risk_flags:
    weight = hierarchy_weights[flag.flag_type]
    strength = strength_scores[flag.signal_strength]

    flag_score = weight * strength

    qualitative_score = max(
        qualitative_score,
        flag_score
    )
```

The strongest flag determines the base qualitative score.

Positive feedback has a weight of 0.00 and therefore cannot reduce the score.

#### 4.5.1 Recurrence Bonus

Recurrence is already calculated by Node 3.

Node 4 consumes the maximum `recurrence_count` reported by Node 3 and applies a small deterministic bonus:

```python
max_recurrence_count = max([flag.recurrence_count for flag in customer.risk_flags], default=0)

recurrence_bonus = min(0.20, 0.05 * max(0, max_recurrence_count - 1))

qualitative_score = min(1.0, qualitative_score + recurrence_bonus)
```

**Examples:**

| Occurrences | Bonus |
|---|---|
| 1 | +0.00 |
| 2 | +0.05 |
| 3 | +0.10 |
| 4 | +0.15 |
| 5+ | +0.20 |

Node 4 must not apply another recency-decay calculation.

The resulting qualitative score must satisfy:

```
0.0 <= qualitative_score <= 1.0
```

### 4.6 Strongest Qualitative Signal

For deterministic comparisons, signal strength is ordered:

```
strong > moderate > weak > none
```

This ordering is represented by the configuration:

```
strength_order = {
    "none": 0,
    "weak": 1,
    "moderate": 2,
    "strong": 3
}
```

When multiple qualitative signals exist, the strongest signal is determined using this ordering.

- If two flags have the same signal strength, the hierarchy weight is used as the next comparison.
- If they also have the same hierarchy weight, the flag with the highest recurrence count is considered stronger.
- If all values remain tied, the flag type is ordered lexicographically for deterministic behavior.

### 4.7 Combined Score

The combined score merges the quantitative and qualitative components.

```
combined_score = (
    quantitative_weight * (quantitative_score or 0.0)
    +
    qualitative_weight * qualitative_score
)
```

The two weights must sum to 1.0:

```
quantitative_weight + qualitative_weight = 1.0
```

#### 4.7.1 Strong Agreement Bonus

Strong agreement is explicitly defined as:

```
strong_agreement = (
    quantitative_score is not None
    and quantitative_score >= quantitative_thresholds["high"]
    and qualitative_signal_strength == "strong"
)
```

When `strong_agreement` is true:

```
combined_score += agreement_bonus
```

The final score is bounded:

```
combined_score = max(
    0.0,
    min(1.0, combined_score)
)
```

The agreement bonus may increase the combined score but can never directly create a critical classification.

### 4.8 Definition of Significant Risk Flag

For Critical-rule evaluation, a significant risk flag is any Node 3 `AggregatedRiskFlag` satisfying at least one of the following:

- `severity ∈ {medium, high}`

OR

- `signal_strength ∈ {moderate, strong}`

Therefore, a flag is significant when it has either meaningful severity or meaningful signal strength.

A `positive_feedback` flag is never considered a significant risk flag.

### 4.9 Risk Level Assignment

Risk level assignment happens after all scores and qualitative attributes have been computed.

The order is:

```
Critical
    ↓
High
    ↓
Medium
    ↓
Low
    ↓
Insufficient Data
```

Critical rules are evaluated first.

### 4.10 Critical Classification

A customer is classified as critical if any one of the following explicit rules is satisfied.

The combined score alone can never produce critical.

**Critical Rule 1: Moderate or Strong Cancellation Intent**

- `cancellation_intent`
- AND `signal_strength ∈ {moderate, strong}`

This is sufficient by itself.

*Example:* `cancellation_intent` with `signal_strength = moderate` → **critical**

**Critical Rule 2: Strong Cancellation Plus Another Significant Risk Flag**

- `cancellation_intent`
- AND `signal_strength = strong`
- AND at least one other significant risk flag exists

The additional flag must be a different `flag_type`.

This rule provides additional explicit evidence of a serious situation.

A weak cancellation signal does not become Critical merely because another weak flag exists.

**Critical Rule 3: Very High Quantitative Risk Plus Strong Contract Concern**

- `quantitative_score >= quantitative_thresholds["high"]`
- AND `renewal_or_contract_concern` exists
- AND `renewal_or_contract_concern.signal_strength == "strong"`

This allows a very strong quantitative signal combined with strong contractual or renewal evidence to produce Critical without explicit cancellation language.

**Critical Rule 4: Repeated High-Severity Support Problems Plus Strong Quantitative Risk**

- `quantitative_score >= quantitative_thresholds["high"]`
- AND at least one risk flag satisfies:
  - `severity == "high"`
  - AND `recurrence_count >= 2`

This represents repeated serious support problems combined with very high quantitative risk.

### 4.11 Critical Rule Recording

Every Critical customer must have at least one explicit critical rule recorded in `primary_reasons`.

The `reason_type` must identify the rule.

**Allowed Critical reason types:**

- `critical_cancellation_intent`
- `critical_cancellation_plus_significant_flag`
- `critical_high_quant_plus_contract_concern`
- `critical_repeated_high_severity_plus_high_quant`

If multiple Critical rules are satisfied, all applicable rules may be recorded. At least one must always be present.

### 4.12 High, Medium, and Low Classification

Customers that do not satisfy a Critical rule are classified using the combined score.

```
if combined_score >= risk_thresholds["high"]:
    level = "high"

elif combined_score >= risk_thresholds["medium"]:
    level = "medium"

else:
    level = "low"
```

Therefore:

| Combined Score | Level |
|---|---|
| ≥ 0.70 | High |
| 0.40 ≤ score < 0.70 | Medium |
| < 0.40 | Low |

These values are configuration-driven and may change only through a new `threshold_version`.

### 4.13 Insufficient Data Classification

A customer is placed in the separate `insufficient_data_accounts` list when there is insufficient usable evidence from both upstream nodes.

**The primary rule is:**

```
Node 2 model_status ∈ {INSUFFICIENT_DATA, FAILED}
AND
Node 3 support_data_status == "no_data"
```

A customer may also be classified as insufficient data when both sides technically exist but neither provides enough usable evidence to support a meaningful judgment.

For implementation, this secondary condition must be represented explicitly by the upstream status values and must not be inferred from a low score alone.

**Important:**
- No support data ≠ low risk
- No quantitative score ≠ low risk

If either Node 2 or Node 3 contains meaningful evidence, the customer remains eligible for the main ranked list.

### 4.14 Missing Upstream Node Behavior

#### 4.14.1 Node 3 Unavailable

If Node 3 is unavailable:

```
qualitative_score = 0.0
qualitative_confidence = 0.0
```

The Node 2 quantitative result remains unchanged. Combined confidence decreases accordingly.

A warning must be added:

> "Node 3 unavailable; quantitative-only synthesis used."

Node 4 must not interpret the absence of Node 3 as positive evidence.

#### 4.14.2 Node 2 Unavailable

If Node 2 is unavailable:

```
quantitative_score = None
quant_confidence = 0.0
```

The qualitative score from Node 3 remains usable.

Strong qualitative evidence may still produce **High** or **Critical** when the explicit Critical rules are satisfied.

A warning must be added:

> "Node 2 unavailable; qualitative-only synthesis used."

### 4.15 Confidence Calculation

Risk level and confidence are independent.

A customer may therefore be **HIGH risk + LOW confidence** or **LOW risk + HIGH confidence**.

The quantitative confidence proxy is determined by Node 2 status:

```
QUANT_CONFIDENCE_BY_STATUS = {
    "READY": 1.00,
    "WARNING": 0.70,
    "FALLBACK": 0.45,
    "INSUFFICIENT_DATA": 0.00,
    "FAILED": 0.00
}
```

Then:

```
quant_confidence = QUANT_CONFIDENCE_BY_STATUS[node2.model_status]
```

The qualitative confidence is taken directly from Node 3:

```
qualitative_confidence = node3.overall_signal_confidence
```

Combined confidence:

```
combined_confidence = (
    confidence_weights["quantitative"] * quant_confidence
    +
    confidence_weights["qualitative"] * qualitative_confidence
)
```

The final value must be bounded:

```
0.0 <= combined_confidence <= 1.0
```

and rounded deterministically to three decimal places.

### 4.16 Primary Reasons

Primary reasons are deterministic structured objects.

They must explain why the customer received the resulting classification without relying on an LLM.

**Examples of reason types:**

```
critical_cancellation_intent
critical_cancellation_plus_significant_flag
critical_high_quant_plus_contract_concern
critical_repeated_high_severity_plus_high_quant
high_quantitative_risk
moderate_quantitative_risk
strong_support_signal
moderate_support_signal
repeated_support_issue
high_support_urgency
limited_support_data
missing_support_data
missing_quantitative_data
quantitative_qualitative_agreement
quantitative_qualitative_conflict
```

Only applicable reasons are included.

The reason structure is:

```json
{
    "reason_type": str,
    "source": "node2" | "node3",
    "severity": str,
    "evidence_ref": str | dict
}
```

Human-readable reason strings are generated deterministically from these structured values.

The LLM is not responsible for generating the underlying reasons.

### 4.17 Conflict Handling

Node 4 must preserve conflicts rather than hiding them.

**High Quantitative Risk + Positive Support**

Result: Risk level is determined by the deterministic rules. Conflict is recorded in `primary_reasons`. Positive feedback does not mathematically reduce the quantitative risk.

**Low Quantitative Risk + Strong Cancellation**

Strong cancellation evidence takes priority. If Critical Rule 1 is satisfied: `combined_risk_level = critical`. The low quantitative score does not override the explicit cancellation evidence.

**High Quantitative Risk + Node 3 No Data**

The customer can still be **high** or **critical** if an explicit Critical rule is satisfied. Confidence is reduced because qualitative evidence is unavailable.

### 4.18 Ranking Procedure

Ranking is performed only after all customers have been fully evaluated.

The procedure is:

1. Build the complete customer universe.
2. Compute quantitative values.
3. Compute qualitative values.
4. Compute combined score.
5. Evaluate Critical rules.
6. Assign risk level.
7. Compute confidence.
8. Generate deterministic reasons.
9. Separate insufficient-data customers.
10. Sort the main list.
11. Assign sequential ranks starting from 1.

### 4.19 Main Ranking Sort Order

The main ranked list contains: Critical, High, Medium, Low.

Sorting is deterministic using the following order:

1. **Risk level** — Critical > High > Medium > Low
2. **Combined score** — descending
3. **Cancellation intent** — customers with `cancellation_language_detected == True` are placed first
4. **Strongest qualitative signal** — using `strong > moderate > weak > none`
5. **Quantitative risk** — descending
6. **Combined confidence** — descending
7. **Customer ID** — ascending lexicographical order

This final key guarantees deterministic ordering even when every other field is identical.

### 4.20 Rank Assignment

Ranks are assigned only after sorting.

- The first customer receives `rank = 1`
- The second receives `rank = 2`, and so on.

Insufficient-data customers do not receive a rank in the main list. Their rank value is `None`.

### 4.21 Output Schema (RankedAccount)

```python
{
    "customer_id": str,
    "rank": int | None,
    "combined_risk_level": "critical" | "high" | "medium" | "low" | "insufficient_data",
    "combined_score": float,
    "combined_confidence": float,
    "quantitative": {
        "model_status": str,
        "risk_score": float | None,
        "survival_prob_90d": float | None,
        "normalized_risk": float | None,
        "top_drivers": list[str],
        "customer_state": str,
    },
    "qualitative": {
        "support_data_status": str,
        "signal_strength": str,
        "overall_signal_confidence": float,
        "churn_language_detected": bool,
        "top_flags": list,
        "escalation_signal": bool,
    },
    "primary_reasons": list[StructuredReason],
    "evidence_refs": {
        "node2": {"model_version": str, "customer_state": str, "feature_refs": list},
        "node3": {"signal_version": str, "thread_ids": list[str], "message_ids": list[str]},
    },
    "explanation": str | None,
    "meta": {
        "ranked_at": str,
        "ranking_version": str,
        "threshold_version": str,
        "critical_rules_version": str,
    },
}
```

### 4.22 StructuredReason

```python
{"reason_type": str, "source": "node2" | "node3", "severity": str, "evidence_ref": str | dict}
```

- `reason_type` must identify the deterministic reason.
- `source` identifies whether the reason originated from Node 2 or Node 3.
- `severity` represents the severity associated with the reason.
- `evidence_ref` points to the underlying evidence needed to reconstruct the decision.

Human-readable reason strings must be generated deterministically from this structure.

### 4.23 Evidence References

**Node 2 evidence references must contain:**

```python
{"model_version": str, "customer_state": str, "feature_refs": list}
```

**Node 3 evidence references must contain:**

```python
{"signal_version": str, "thread_ids": list[str], "message_ids": list[str]}
```

Evidence references must point back to the actual upstream evidence.

Node 4 must not create fabricated evidence references.

If evidence is unavailable, the reference must explicitly represent that absence.

### 4.24 Full Node 4 Output

```python
{
    "ranked_accounts": list[RankedAccount],
    "insufficient_data_accounts": list[RankedAccount],
    "summary_stats": {
        "n_customers": int,
        "n_critical": int,
        "n_high": int,
        "n_medium": int,
        "n_low": int,
        "n_insufficient_data": int,
    },
    "processing_report": {"warnings": list[str], "errors": list[dict]},
}
```

The summary counts must equal the actual number of records in the corresponding output lists.

### 4.25 LLM Explanation Rules

LLM use is optional.

**If enabled, the LLM receives only:**
- already-computed risk level
- combined score
- combined confidence
- structured reasons
- approved evidence references
- relevant evidence text

**The LLM is forbidden from:**
- changing `combined_score`
- changing `combined_confidence`
- changing `combined_risk_level`
- changing `rank`
- creating new evidence
- deleting evidence
- creating new reasons
- overriding Node 2
- overriding Node 3
- re-evaluating Critical rules

The LLM may only convert the deterministic decision information into a short natural-language explanation.

If the LLM is unavailable or fails: `explanation = None`. The rest of Node 4 must continue normally.

The deterministic output must remain complete and valid without any LLM involvement.

### 4.26 Failure Handling

Node 4 must not silently discard customers.

- **Customer appears in Node 2 but not Node 3:** proceed with quantitative-only synthesis.
- **Customer appears in Node 3 but not Node 2:** proceed with qualitative-only synthesis when meaningful qualitative evidence exists.
- **Customer appears in neither input:** the customer cannot exist in the Node 4 universe.
- **Malformed upstream record:** add an error to `processing_report`; the error must identify the affected customer when possible; processing should continue for unaffected customers.

### 4.27 Required Invariants

The implementation must include tests proving all of the following.

- **Determinism** — Same inputs plus same configuration versions produce identical output.
- **Customer Universe** — Every customer appearing in Node 2 or Node 3 appears exactly once in either `ranked_accounts` or `insufficient_data_accounts`.
- **Rank Stability** — Adding an unrelated customer does not change an existing customer's risk level, combined score, confidence, or the relative ordering of existing customers, unless the newly added customer is itself inserted ahead of them, which may change their absolute rank number.
- **Explanation Independence** — Changing or removing the LLM explanation does not change score, confidence, risk level, rank, or reasons.
- **LLM Failure** — LLM unavailable must not prevent Node 4 from producing the deterministic result.
- **Node 3 Failure** — Node 3 unavailable must not prevent quantitative-only synthesis.
- **Node 2 Failure** — Node 2 unavailable must not prevent strong qualitative signals from producing High or Critical when the explicit rules allow it.
- **Critical Rule Requirement** — Every Critical customer must have at least one qualifying Critical rule in `primary_reasons`.
- **Critical Score Protection** — A high combined score alone must never produce Critical.
- **Positive Feedback Protection** — Positive feedback must never reduce quantitative risk or qualitative risk.
- **No-Data Protection** — Node 3 `no_data` must never be interpreted as evidence that the customer is healthy.
- **Evidence Traceability** — Every Critical customer must have sufficient evidence references to reconstruct the reason for the Critical classification.
- **Rank Independence** — Changing explanation text must never alter ranking.

### 4.28 Versioning Requirements

Every Node 4 run must record:
- `ranking_version`
- `threshold_version`
- `critical_rules_version`
- `reference_date`

The output must also preserve the relevant upstream versions:
- Node 2 `model_version`
- Node 3 `signal_version`

A change to any scoring, threshold, ranking, Critical-rule, or evidence-selection behavior requires an appropriate version change.

### 4.29 What Node 4 Does Not Do

Node 4 does not:
- Write the final client-facing report.
- Modify Node 2 scores.
- Modify Node 3 signals.
- Override upstream evidence.
- Use an LLM for scoring.
- Use an LLM for ranking.
- Use an LLM for risk classification.
- Use an LLM to determine Critical status.
- Treat Node 3 `no_data` as positive evidence.
- Treat positive feedback as a mathematical risk reduction.
- Allow the combined score alone to create Critical.
- Drop customers that appear in only one upstream node.
- Reapply Node 3's recency decay.
- Invent evidence.
- Invent customer IDs.
- Invent message IDs.
- Hide conflicts between quantitative and qualitative signals.

### 4.30 Final Decision Flow

The complete deterministic flow is:

```
Node 2 Output ──────────────┐
                            │
                            ▼
                     Customer Union
                            ▲
                            │
Node 3 Output ──────────────┘
                            │
                            ▼
                 Quantitative Normalization
                            │
                            ▼
                  Qualitative Signal Score
                            │
                            ▼
                    Combined Score
                            │
                            ▼
                  Critical Rule Evaluation
                            │
                ┌───────────┴───────────┐
                │                       │
             Critical              Not Critical
                │                       │
                │                       ▼
                │                High / Medium / Low
                │                       │
                └───────────┬───────────┘
                            ▼
                  Confidence Calculation
                            │
                            ▼
                 Deterministic Reasons
                            │
                            ▼
                   Evidence References
                            │
                            ▼
                Insufficient Data Split
                            │
                            ▼
                  Deterministic Sorting
                            │
                            ▼
                    Sequential Ranking
                            │
                            ▼
                  Optional LLM Explanation
                            │
                            ▼
                       Node 4 Output
                            │
                            ▼
                         Node 5
```

This is now locked as the Node 4 implementation contract.

---

## 5. Node 5 — Client-Facing Risk Report

**Version:** 1.0 (Implementation-Locked)
**Date:** 2026-08-17
**Status:** Locked. Ready for implementation.
This is the authoritative specification for Node 5. Node 5 converts the deterministic decision-support output from Node 4 into a client-facing report while preserving the complete evidence and decision chain.

### 5.0 Purpose & Position

Node 5 produces the final client-facing customer-risk report.

It consumes the ranked and explainable decisions produced by Node 4 and converts them into:
- A management summary
- A prioritized customer list
- Customer-level explanations
- Quantitative and qualitative reasons
- Supporting evidence
- Data-quality warnings
- Recommended follow-up actions, where explicitly permitted by configuration

Node 5 does not perform new risk analysis.

It does not:
- Recalculate risk
- Change customer rankings
- Change risk levels
- Override Node 4
- Interpret raw support conversations independently
- Recalculate Node 2 survival probabilities
- Recalculate Node 3 support signals
- Invent evidence
- Invent customer facts
- Decide that a customer is risky when Node 4 did not

### 5.1 Pipeline Position

```
Node 1 → Canonical customer data
Node 2 → Statistical risk
Node 3 → Qualitative evidence
Node 4 → Deterministic synthesis
Node 5 → Client-facing report
```

Node 5 is the presentation and communication layer.

The authoritative decision remains Node 4.

### 5.2 Core Principles

#### 5.2.1 Node 4 is the Source of Truth

Every customer risk statement in the report must originate from Node 4.

Node 5 may transform:

```
StructuredReason
        ↓
Human-readable explanation
```

It may not transform:

```
No risk
        ↓
High risk
```

or:

```
High
        ↓
Critical
```

#### 5.2.2 No Analytical Decisions Inside Node 5

The LLM must never be asked: "Which customers are at risk?"

Instead it receives: "Here are the customers already classified by Node 4. Write a clear explanation of these existing decisions."

This distinction is important.

#### 5.2.3 Evidence Must Remain Traceable

Every material claim in the report must be traceable to:

```
Customer
    ↓
Node 4 decision
    ↓
Node 2 / Node 3 evidence
    ↓
Original source
```

For qualitative evidence this ultimately reaches:

```
thread_id
    ↓
message_id
    ↓
timestamp
    ↓
evidence text
```

For quantitative evidence it reaches:

```
Node 2
    ↓
model version
    ↓
customer
    ↓
feature association / score
```

### 5.3 Input Contract

Node 5 receives:

```python
{
    "node4_output": dict,
    "customer_data": dict | None,
    "config": {
        "report_version": str,
        "prompt_version": str,
        "model_version": str | None,
        "reference_date": str,
        "include_insufficient_data": bool,
        "include_evidence": bool,
        "include_recommendations": bool,
        "max_accounts_in_summary": int,
        "max_evidence_per_account": int,
        "language": str,
    },
}
```

The primary input is `node4_output`.

`customer_data` is optional and exists only for safe contextual information such as:
- customer name
- account name
- customer segment
- industry
- account owner

If this information is not available, Node 5 must use `customer_id`.

Node 5 must never use raw customer data to independently calculate risk.

### 5.4 Node 4 Validation

Before generating anything, Node 5 performs deterministic validation.

**Required checks:**
- Node 4 output exists.
- Output schema is valid.
- Every ranked account has a valid customer ID.
- Risk level is one of: `critical`, `high`, `medium`, `low`, `insufficient_data`.
- Combined score is between 0 and 1.
- Combined confidence is between 0 and 1.
- Every Critical account has a qualifying critical reason.
- Evidence references have valid structure.
- Summary statistics match the actual account lists.
- Ranking numbers are sequential.
- No customer appears twice in the main ranked list.
- No customer appears in both the main list and insufficient-data list.

If validation fails, Node 5 must not silently continue. The run should fail safely and record the validation errors.

### 5.5 Report Structure

The final report contains:

```
Executive Summary
        ↓
Risk Distribution
        ↓
Priority Accounts
        ↓
Detailed Account Analysis
        ↓
Insufficient Data
        ↓
Methodology & Data Quality
        ↓
Report Metadata
```

### 5.6 Executive Summary

The report begins with a concise management-level summary.

**Example:**

```
Executive Summary

The analysis identified 12 priority accounts across the current
customer portfolio.

3 accounts were classified as Critical,
4 as High,
3 as Medium,
2 as Low.

The highest-priority accounts were primarily driven by explicit
cancellation intent, elevated quantitative risk, and repeated
support-related issues.

2 customers had insufficient data for a reliable assessment.
```

These numbers must be generated deterministically from Node 4. The LLM may polish wording, but it cannot change the numbers.

### 5.7 Risk Distribution

Node 5 produces a deterministic summary:

```python
{"critical": 3, "high": 4, "medium": 3, "low": 3, "insufficient_data": 2}
```

The report may display this as:

| Risk Level | Customers |
|---|---|
| Critical | 3 |
| High | 4 |
| Medium | 3 |
| Low | 3 |
| Insufficient data | 2 |

No LLM is needed for this section.

### 5.8 Priority Account Section

The main report contains accounts in exactly the order supplied by Node 4.

Node 5 must never re-sort them.

**Example:**

```
Priority Accounts

1. Acme Corp
   Risk: Critical
   Confidence: 0.91

2. Example Ltd
   Risk: Critical
   Confidence: 0.84

3. Customer ABC
   Risk: High
   Confidence: 0.79
```

The rank comes directly from Node 4.

### 5.9 Customer Report Schema

Internally Node 5 should generate a structured representation before producing the final document.

```python
{
    "customer_id": str,
    "display_name": str,
    "rank": int,
    "risk_level": "critical" | "high" | "medium" | "low",
    "combined_score": float,
    "combined_confidence": float,
    "headline": str,
    "summary": str,
    "primary_reasons": list[ReportReason],
    "quantitative_summary": {
        "risk_score": float | None,
        "survival_prob_90d": float | None,
        "top_drivers": list[str],
        "customer_state": str,
    },
    "support_summary": {
        "support_data_status": str,
        "signal_strength": str,
        "churn_language_detected": bool,
        "escalation_signal": bool,
        "top_flags": list[ReportFlag],
    },
    "evidence": list[ReportEvidence],
    "data_quality_notes": list[str],
    "recommended_action": str | None,
}
```

### 5.10 ReportReason

```python
{
    "reason_type": str,
    "source": "node2" | "node3",
    "severity": "low" | "medium" | "high" | "critical",
    "statement": str,
    "evidence_ref": str | dict,
}
```

The `statement` is generated from existing structured information.

For example:

> Explicit cancellation intent detected in support interactions.

It must correspond to an actual Node 4 reason.

### 5.11 Evidence Representation

```python
{
    "source": "node2" | "node3",
    "description": str,
    "node2_reference": {"model_version": str, "feature_ref": str | None} | None,
    "node3_reference": {"thread_id": str, "message_id": str, "timestamp": str, "evidence_text": str}
    | None,
}
```

Node 5 may shorten evidence for presentation, but the underlying reference must remain available.

### 5.12 Evidence Rules

Evidence is handled differently depending on source.

**Node 2**

The report can say:

```
Quantitative driver:
90-day survival probability: 0.21
```

and:

```
Primary model drivers:
- Declining usage
- Reduced activity
- Contract activity
```

Only if those values exist in Node 4.

**Node 3**

The report can say:

```
Support evidence:
Customer expressed cancellation intent in a recent support interaction.
```

If direct quotations are included, they must come from Node 3 evidence.

Node 5 must never invent a quotation.

### 5.13 Evidence Privacy / Presentation Rule

The internal evidence object may contain the original message text.

The client-facing report does not necessarily need to expose the entire message.

Configuration controls whether evidence is:
- disabled
- summary only
- short quote
- full evidence

Default: `short quote / paraphrase`

The underlying evidence reference remains available internally.

### 5.14 Customer Summary Generation

The summary should be short, normally 2 to 4 sentences.

**Example:**

> Acme Corp is classified as Critical primarily because explicit cancellation intent was detected in recent support interactions. This is reinforced by elevated quantitative risk from the survival model and repeated product-related issues.

This is explanation, not new analysis.

### 5.15 LLM Contract

The LLM receives only validated Node 4 information.

Conceptually:

```python
{
    "customer_id": "...",
    "risk_level": "critical",
    "rank": 1,
    "combined_score": 0.87,
    "combined_confidence": 0.91,
    "primary_reasons": [...],
    "quantitative": {...},
    "qualitative": {...},
    "evidence": [...],
}
```

The prompt explicitly instructs:

> You are generating client-facing explanatory text.
>
> You must only describe information contained in the supplied structured input.
>
> You must not:
> - change risk levels
> - change scores
> - change rankings
> - invent evidence
> - invent customer facts
> - infer unsupported causes
> - add new risk factors
> - introduce unsupported recommendations
> - contradict the structured input
>
> If information is unavailable, do not speculate.

### 5.16 LLM Output

The LLM should return structured JSON rather than free-form text.

```python
{"headline": str, "summary": str, "reason_explanations": list[str]}
```

Pydantic validates the result.

The LLM does not return:

```python
{"risk_level": ..., "rank": ..., "score": ...}
```

Those values already exist and must never be regenerated.

### 5.17 Deterministic Fallback

If the LLM:
- fails
- times out
- returns invalid JSON
- violates the schema
- introduces unsupported claims

Node 5 uses deterministic templates.

For example:

> Critical priority due to explicit cancellation intent detected in support interactions.

The report remains fully usable.

Therefore: **LLM failure must never prevent report generation.**

### 5.18 Recommended Actions

Node 5 should not initially generate arbitrary recommendations with an LLM.

Instead, use deterministic mappings.

**Example:**

```python
ACTION_RULES = {
    "cancellation_intent": "Contact the account to discuss cancellation concerns.",
    "renewal_or_contract_concern": "Review the upcoming renewal or contract status.",
    "product_bug_or_outage": "Review unresolved product issues with the relevant support or product team.",
    "poor_support_experience": "Review recent support interactions and unresolved complaints.",
    "billing_complaint": "Review billing issues and outstanding disputes.",
    "feature_missing": "Review the requested functionality and available alternatives.",
}
```

These mappings should be versioned.

This keeps recommendations auditable.

### 5.19 Recommendation Priority

If recommendations are enabled, Node 5 selects them from Node 4 reasons.

**Priority:**
1. `cancellation_intent`
2. `renewal_or_contract_concern`
3. `product_bug_or_outage`
4. `poor_support_experience`
5. `billing_complaint`
6. `usage_drop_related`
7. `feature_missing`
8. `competitor_mention`
9. `other`

Node 5 does not invent a recommendation for a reason that does not exist.

### 5.20 Insufficient Data

Insufficient-data customers are kept separate from the primary ranked accounts.

**Example:**

```
Insufficient Data

The following accounts could not be assessed reliably because
available quantitative and qualitative evidence was insufficient.

Customer A
Customer B
```

Important: **Insufficient data ≠ low risk**

The report must make this explicit.

### 5.21 Conflicting Evidence

Node 4 may identify situations such as **High quantitative risk + Positive support sentiment**.

Node 5 must preserve that conflict.

**Example:**

> The account is classified as High risk based primarily on the quantitative model. Recent support interactions were generally positive, creating a divergence between quantitative and qualitative signals.

It must not rewrite this as: "The customer is doing well."

### 5.22 Confidence Communication

Confidence should be displayed separately from risk.

For example:

```
Risk: HIGH
Confidence: 0.62
```

And `Risk: CRITICAL / Confidence: 0.41` is valid.

The report should explain low confidence when relevant:

> The risk classification is supported by limited available data.

It must not say:

> There is only a 41% chance the customer is high risk.

Confidence is not probability of churn.

### 5.23 Data Quality Section

The report should contain a short data-quality section.

**Example:**

```
Data Quality

The analysis used:
- Quantitative survival-model results from Node 2
- Support interaction signals from Node 3
- Reference date: 2026-08-17

2 accounts had insufficient data.
3 accounts had limited support data.
```

This information comes from the upstream outputs.

### 5.24 Methodology Section

The final report should include a concise methodology section.

**Example:**

```
Methodology

Customer risk was synthesized from two independent sources.

Node 2 provides quantitative survival-risk estimates.

Node 3 extracts structured qualitative signals from customer
support interactions.

Node 4 combines these signals using deterministic, versioned
rules. Risk levels and rankings are therefore not generated by
the language model.

Node 5 converts these structured decisions into client-facing
language.
```

This is useful for auditability and client trust.

### 5.25 Report Metadata

Every report records:

```python
{
    "report_version": str,
    "node2_model_version": str,
    "node3_signal_version": str,
    "node4_ranking_version": str,
    "node4_threshold_version": str,
    "node4_critical_rules_version": str,
    "prompt_version": str | None,
    "llm_model_version": str | None,
    "reference_date": str,
    "generated_at": str,
}
```

This allows a report to be reproduced later.

### 5.26 Full Node 5 Output Contract

The internal output:

```python
{
    "report": {
        "title": str,
        "reference_date": str,
        "executive_summary": str,
        "risk_distribution": {
            "critical": int,
            "high": int,
            "medium": int,
            "low": int,
            "insufficient_data": int,
        },
        "priority_accounts": list[CustomerReport],
        "insufficient_data_accounts": list[CustomerReport],
        "data_quality": {"notes": list[str]},
        "methodology": str,
    },
    "metadata": {
        "report_version": str,
        "node2_model_version": str,
        "node3_signal_version": str,
        "node4_ranking_version": str,
        "node4_threshold_version": str,
        "node4_critical_rules_version": str,
        "prompt_version": str | None,
        "llm_model_version": str | None,
        "reference_date": str,
        "generated_at": str,
    },
    "processing_report": {
        "n_accounts": int,
        "n_accounts_reported": int,
        "n_insufficient_data": int,
        "llm_calls": int,
        "llm_failures": int,
        "validation_errors": int,
        "warnings": list[str],
        "errors": list[dict],
    },
}
```

### 5.27 Deterministic Report Generation

The pipeline should effectively be:

```
Node 4 output
      ↓
Pydantic validation
      ↓
Normalize report data
      ↓
Build deterministic sections
      ↓
Generate structured explanations
      ↓
Pydantic validation
      ↓
Validate explanations against source data
      ↓
Build final report
      ↓
Final consistency validation
      ↓
PDF / HTML / JSON
```

### 5.28 Explanation Validation

This is one of the most important safeguards.

After the LLM produces an explanation, Node 5 should validate it against the structured source.

At minimum check that the explanation does not introduce:
- a different risk level
- a different ranking
- unsupported numerical values
- unsupported customer facts
- unsupported risk factors
- unsupported evidence
- unsupported recommendations

For example, if Node 4 says `cancellation_intent = false` and the LLM says "The customer has indicated that they want to cancel", the explanation must be rejected.

### 5.29 Final Report Consistency Checks

Before publishing:
- Does displayed rank equal Node 4 rank?
- Does displayed risk equal Node 4 risk?
- Does displayed score equal Node 4 score?
- Does displayed confidence equal Node 4 confidence?
- Do customer counts match?
- Are all Critical customers supported by Node 4 critical rules?
- Are insufficient-data customers separated?
- Are evidence references valid?
- Are report versions recorded?

If any mandatory check fails: **Do not publish.**

### 5.30 Output Formats

Node 5 should ideally produce three outputs from the same structured report:
- **JSON** — machine-readable and audit-friendly.
- **HTML** — useful for a dashboard or web interface.
- **PDF** — client-facing document.

The important part is that all three are generated from the same validated structured report. Do not have separate logic for PDF and HTML.

### 5.31 Suggested Client Report Layout

```
--------------------------------------------------
Customer Risk Report
Reference Date: 17 August 2026
--------------------------------------------------

EXECUTIVE SUMMARY

12 customers analyzed
3 Critical
4 High
3 Medium
3 Low
2 Insufficient Data

RISK DISTRIBUTION

[summary]

PRIORITY ACCOUNTS

#1  Acme Corp
    CRITICAL
    Confidence: 91%

    Why this account matters
    ...

    Quantitative signals
    ...

    Support signals
    ...

    Evidence
    ...

#2  Example Ltd
    HIGH
    ...

INSUFFICIENT DATA

...

DATA QUALITY

...

METHODOLOGY

...

REPORT METADATA

...
```

### 5.32 What Node 5 Explicitly Does Not Do

Node 5 does not:
- Calculate survival probability
- Calculate churn probability
- Calculate support sentiment
- Extract support flags
- Recalculate qualitative strength
- Recalculate combined risk
- Change risk levels
- Change rankings
- Override Node 4
- Search external sources for additional evidence
- Invent customer information
- Treat confidence as probability
- Treat no-data as low risk
- Use LLM output as a source of truth
- Make unsupported recommendations

### 5.33 Evaluation & Testing

Before production, create a test set covering:

**Normal cases:**
- Critical customer
- High customer
- Medium customer
- Low customer
- Insufficient-data customer

**Conflicting cases:**
- High quantitative + positive support
- Low quantitative + cancellation intent
- Strong qualitative + missing quantitative
- Strong quantitative + missing qualitative

**LLM failures:**
- Timeout
- Invalid JSON
- Missing fields
- Hallucinated evidence
- Changed risk level
- Changed score
- Unsupported recommendation

**Data problems:**
- Missing customer name
- Missing evidence
- Missing Node 2
- Missing Node 3
- Duplicate customer
- Invalid Node 4 schema

### 5.34 Critical Invariants

These should be automated tests.

1. Node 5 cannot change Node 4 risk levels.
2. Node 5 cannot change Node 4 ranks.
3. Node 5 cannot change Node 4 scores.
4. Node 5 cannot change Node 4 confidence.
5. Removing the LLM produces a valid report.
6. Changing LLM wording does not change any decision.
7. Invalid LLM output falls back safely.
8. Every reported evidence item exists upstream.
9. Every Critical account retains its Node 4 critical reason.
10. Insufficient-data accounts remain separate.
11. Report statistics exactly match Node 4.
12. Same validated inputs and versions produce the same deterministic report structure.

### 5.35 The Most Important Architectural Decision

Keep the actual implementation split into three components:

```
node5/
├── validation/
│   └── node4_validator.py
│
├── report/
│   ├── transformer.py
│   ├── deterministic_sections.py
│   └── evidence.py
│
├── llm/
│   ├── explainer.py
│   └── schemas.py
│
├── rendering/
│   ├── html.py
│   ├── pdf.py
│   └── json.py
│
└── node.py
```

The important separation is:

```
Decision      →  Node 4
Explanation   →  Node 5 LLM
Presentation  →  Node 5 renderer
```

That keeps the architecture clean.

### 5.36 Recommended Implementation Flow

Implement Node 5 in this order:

**Phase 1 — Schemas:** Create Pydantic models for `Node4Input`, `CustomerReport`, `ReportReason`, `ReportEvidence`, `ReportMetadata`, `ReportOutput`, `ProcessingReport`.

**Phase 2 — Validation:** Implement `validate_node4_output()`, `validate_customer()`, `validate_evidence_refs()`, `validate_summary_stats()`.

**Phase 3 — Deterministic transformation:** Implement `build_executive_summary()`, `build_risk_distribution()`, `build_customer_report()`, `build_data_quality_section()`, `build_methodology_section()`. At this point you already have a complete report without AI.

**Phase 4 — Deterministic recommendations:** Implement the controlled reason → action mappings.

**Phase 5 — LLM explanation:** Add the LLM only after the deterministic version works.

**Phase 6 — LLM validation:** Validate generated explanations and fall back to templates when necessary.

**Phase 7 — Rendering:** Generate JSON, HTML, and PDF from the same report object.

**Phase 8 — Tests:** Run the invariants and failure-mode tests.

---

## 6. Implementation Principles (Non-Negotiable)

1. Deterministic adapters preferred; LLM is a one-time mapping assistant only.
2. Storage of new fields is always allowed; modeling of new fields is gated.
3. The system must be allowed to say "I don't know."
4. All statistical computation stays in ordinary Python functions (lifelines, pandas, numpy, etc.).
5. Orchestration (routing, human confirmation, sequencing) may use LangGraph or equivalent; pure data and statistical steps must not.
6. Every model is fully versioned and reproducible.
7. Tenure and censoring are always defined relative to an explicit observation window and a declared `reference_date`.
8. `risk_scores` is null whenever the model cannot produce individual Cox-style scores.
9. Horizons are data-driven and configurable.
10. Fitting and scoring are separate operations.

---

## 7. Explicitly Out of Scope for Node 1 & Node 2

- Causal claims about churn drivers
- Automatic promotion of `extra_features` into the model
- Naïve random train/test splits as the primary validation method
- Exposing survival horizons the data cannot support
- Using the LLM as a permanent runtime dependency for known formats
- Inventing confidence when the data does not support it
- Treating Kaplan-Meier segment curves as individual risk scores

---

## 8. Recommended Tech Stack (Production-Ready)

Deliberately minimal, reliable, and aligned with every decision locked in. Python 3.11/3.12.

### 8.1 Data & Schema Layer (Node 1)

- **pandas + pyarrow** — primary data handling
- **Pydantic v2** — canonical schema, validation report, mapping report, model output contract (non-negotiable)
- **pandera** (optional but recommended) — additional DataFrame-level validation on top of Pydantic

### 8.2 Survival Analysis (Node 2)

- **lifelines** — `CoxPHFitter` + `KaplanMeierFitter`
- **scikit-learn** — only light utilities (encoding, train/test helpers, VIF if implemented)
- **numpy / scipy** — supporting numerical work

### 8.3 Orchestration & Routing

- **LangGraph** — router + human-in-the-loop confirmation of LLM mappings + sequencing of Node 1 → Node 2
- Keep pure statistical work (fitting, scoring, validation) as ordinary Python functions, not graph nodes

### 8.4 LLM (only for unknown formats)

- Whatever already in use (Anthropic Claude, OpenAI, etc.) via official SDK
- Only called on the fallback path after signature detection fails

### 8.5 Model & Mapping Persistence

- Model artifacts → **joblib or cloudpickle** + a JSON sidecar with all versioning metadata
- Deterministic mapping configs → versioned JSON/YAML files
- Clear directory structure or S3/GCS with a thin metadata index (SQLite or Postgres)
- Later: add MLflow or custom model registry only if the file-based approach starts hurting

### 8.6 Configuration & Secrets

- **pydantic-settings**
- Environment variables + `.env` for local; proper secret manager in production

### 8.7 API / Serving (when exposed)

- **FastAPI** + **uvicorn**

### 8.8 Observability

- **structlog** or **loguru** (structured logging)
- OpenTelemetry only if already in use; otherwise keep it simple

### 8.9 Testing

- **pytest** + **pytest-cov**
- Heavy fixture-based tests for adapters, validation, eligibility rules, and model contracts

### 8.10 Project Structure

```
churn_survival/
├── adapters/               # deterministic adapters + base class
├── schemas/                # Pydantic models (canonical, reports, outputs)
├── router/                 # signature detection + routing logic
├── node1/                  # validation, feature gate prep
├── node2/                  # eligibility, fit, score, fallback
├── models/                 # saved model artifacts + mapping configs
├── orchestration/          # LangGraph graphs
├── api/                    # FastAPI routes (later)
├── tests/
└── pyproject.toml
```

### 8.11 Dependency Highlights (`pyproject.toml` direction)

```
pandas
pyarrow
pydantic>=2
lifelines
scikit-learn
langgraph
langchain-core          # only what LangGraph needs
httpx / anthropic / openai   # depending on LLM provider
joblib
structlog
fastapi
uvicorn
pytest
```

### 8.12 Why This Stack Matches the Architecture

- **Pydantic** enforces the exact contracts (canonical record, validation report, mapping report, final output, model artifact).
- **lifelines** is the library already designed around.
- **LangGraph** is used only for routing + human confirmation, not statistical computation.
- File-based + metadata versioning satisfies strong reproducibility without heavy infrastructure too early.
- Everything stays plain Python functions under the hood, exactly as specified.

---

## 9. Pending Decisions

- [x] Specify **Node 3** scope, contracts, and schemas
- [x] Specify **Node 4** scope, contracts, and schemas
- [x] Specify **Node 5** scope, contracts, and schemas
- [x] Confirm LLM runtime dependency per node (Node 3 uses LLM at thread level only; Node 4 LLM optional with zero authority; Node 5 LLM optional explanation polish only, deterministic fallback mandatory)
- [x] Confirm how Node 2 output is consumed downstream (→ Node 4 synthesis → Node 5 client-facing report)

# Phase 4 Implementation Plan — Node 3 (Support Signal Extraction)

## Goal Description

Implement **Node 3 (Qualitative Support Signal Extraction & Evidence)** of the Churn Survival Analysis System, following **Phase 4 (Tasks 4.1–4.14)** in [ROADMAP.md](file:///C:/Users/Rami/OneDrive/Documents/project/churn_survival/ROADMAP.md) and the locked specifications in [architecture.md](file:///C:/Users/Rami/OneDrive/Documents/project/churn_survival/architecture.md) Section 3.

Node 3 extracts structured qualitative signals from support interactions (threads and messages) and preserves the underlying verifiable evidence for downstream synthesis in Node 4 and reporting in Node 5. Node 3 is completely independent of Node 2.

```
                    Support Data (JSON / Threads)
                                  ↓
    [1] Deterministic Preprocessing (preprocess.py)
        - Window filtering (lookback & future leakage protection)
        - Message cleaning (system message removal, exact dedup)
        - Cross-channel duplicate collapse (TF-IDF + subject similarity)
        - Language detection (supported / unsupported / unknown)
        - Hard limits truncation (max messages, max threads, max tokens)
                                  ↓
    [2] LLM Extraction (llm_extractor.py) — Thread-Level Only
        - Forced structured JSON output with Pydantic validation
        - Temperature ≤ 0.2, token budget tracking
        - Automatic 1-retry on invalid output, then quarantine
                                  ↓
    [3] ThreadSignals Layer (schemas/node3.py) — Audit Evidence
                                  ↓
    [4] Deterministic Aggregation (aggregate.py)
        - Recency decay: exp(-λ * age_days) (λ=0.015 default, 0.004 persistent)
        - Flag aggregation: recurrence counts, temporal span, evidence union
        - Locked formulas: evidence_quality_score & overall_signal_confidence
        - Field derivations: overall_sentiment, urgency_level, escalation_signal,
          churn_language_detected, signal_strength, support_data_status
        - Deterministic templated summary
                                  ↓
    [5] CustomerSupportSignals & Node3Output (node.py & CLI)
```

---

## User Review & Decisions Resolved

> [!NOTE]
> **1. LLM vs. Deterministic Testing (Resolved):**
> Pytest will default to deterministic / mock extraction (0 cost, fast, completely reproducible for CI and local development). A configurable toggle/env var (e.g., `--run-llm` / `RUN_LLM_TESTS`) will allow selectively running live LLM calls against configured providers.

> [!NOTE]
> **2. Summary Templates (Resolved):**
> Template-generated summaries are approved for Node 3. Factual, deterministic sentences will be populated based on the primary flag, recurrence, urgency, and thread counts when flags are present, and `None` when `support_data_status == "no_data"` or no flags are present.

> [!NOTE]
> **3. Single Primary Model (Resolved):**
> Node 3 will use a single primary configured LLM model (via `Settings.LLM_PROVIDER` and `Settings.LLM_MODEL`) with strict structured output, 1 automatic retry on format failure, and robust quarantine handling. Cross-channel duplicate detection refers to detecting when a customer contacts support on multiple channels (e.g., chat + email) about the same issue within 48h.

---

## Proposed Changes

Grouping files by component and execution sequence:

```
churn_survival/
├── config/
│   ├── vocabulary.json                         # [NEW] Controlled flag vocabulary & governance
│   ├── node3/
│   │   └── v1.json                             # [NEW] Default Node 3 configuration
│   └── loader.py                               # [MODIFY] Add load_node3_config & load_vocabulary
├── node3/
│   ├── __init__.py                             # [MODIFY] Export run_node3, preprocess, aggregate
│   ├── vocabulary.py                           # [NEW] Vocabulary ranks & governance helpers
│   ├── preprocess.py                           # [NEW] Cleaning, windowing, dedup, lang detection
│   ├── llm_extractor.py                        # [NEW] Thread-level LLM extraction, budget, retry
│   ├── aggregate.py                            # [NEW] Recency decay, recurrence, formulas, derivations
│   └── node.py                                 # [NEW] Node 3 entry point & CLI subcommand
├── pipeline/
│   └── main.py                                 # [MODIFY] Register and dispatch node3
├── scripts/
│   └── eval_node3_golden.py                    # [NEW] Task 4.13 Cohen's kappa evaluation harness
└── tests/
    └── node3/
        ├── conftest.py                         # [NEW] Support thread test fixtures
        ├── test_config.py                      # [NEW] Task 4.1 config & input validation tests
        ├── test_preprocess.py                  # [NEW] Task 4.2 preprocessing tests
        ├── test_duplicates.py                  # [NEW] Task 4.3 cross-channel duplicate tests
        ├── test_language.py                    # [NEW] Task 4.4 language detection tests
        ├── test_vocabulary.py                  # [NEW] Task 4.6 controlled vocabulary tests
        ├── test_llm_extractor.py               # [NEW] Task 4.7 LLM extraction & retry tests
        ├── test_aggregate.py                   # [NEW] Task 4.8 recency & recurrence tests
        ├── test_formulas.py                    # [NEW] Task 4.9 locked mathematical formula tests
        ├── test_derivations.py                 # [NEW] Task 4.10 customer-level derivations tests
        ├── test_failure_modes.py               # [NEW] Task 4.11 failure modes (§3.12) tests
        ├── test_meta.py                        # [NEW] Task 4.12 versioning metadata tests
        └── test_flow.py                        # [NEW] Task 4.14 end-to-end integration tests
```

---

### Component 1: Configuration & Vocabulary (Tasks 4.1 & 4.6)

#### [NEW] `config/vocabulary.json`
Implements the versioned controlled vocabulary table from §3.4 and governance rules from §3.4 / ROADMAP Task 4.6:
```json
{
  "vocabulary_version": "vocab_v1.0",
  "ranks": {
    "cancellation_intent": 1,
    "renewal_or_contract_concern": 2,
    "product_bug_or_outage": 3,
    "poor_support_experience": 3,
    "billing_complaint": 4,
    "feature_missing": 4,
    "usage_drop_related": 4,
    "competitor_mention": 5,
    "positive_feedback": null,
    "other": null
  },
  "governance": {
    "review_interval_weeks": 4,
    "other_review_threshold_pct": 20.0
  }
}
```

#### [NEW] `config/node3/v1.json`
Default Node 3 configuration conforming to `Node3Config`:
```json
{
  "aggregation_version": "agg_v1.0",
  "vocabulary_version": "vocab_v1.0",
  "preprocessing_version": "pre_v1.0",
  "prompt_version": "prompt_v1.0",
  "lookback_days": 365,
  "max_threads_per_customer": 50,
  "max_messages_per_thread": 100,
  "max_tokens_per_customer": 50000,
  "supported_languages": ["en"],
  "lambda_default": 0.015,
  "lambda_persistent": 0.004,
  "persistent_flag_types": ["cancellation_intent", "renewal_or_contract_concern"],
  "dedup_time_window_hours": 48,
  "dedup_tfidf_threshold": 0.82,
  "dedup_subject_threshold": 0.75,
  "llm_temperature": 0.2,
  "llm_max_retries": 1,
  "limited_data_min_customer_messages": 3,
  "reference_date": "2026-08-15"
}
```

#### [MODIFY] `config/loader.py`
Add `load_node3_config(version: str) -> Node3Config` and `load_vocabulary() -> dict[str, Any]`.

---

### Component 2: Vocabulary Logic & Governance (Task 4.6)

#### [NEW] `node3/vocabulary.py`
- `get_hierarchy_rank(flag_type: str | FlagType) -> int`: Returns rank (1 to 5) or 999 for null ranks.
- `check_vocabulary_governance(flags: Sequence[RiskFlag]) -> list[str]`: Evaluates flag distribution and returns warnings if `other` exceeds 20% of flags.

---

### Component 3: Preprocessing, Deduplication, & Language (Tasks 4.2, 4.3, 4.4)

#### [NEW] `node3/preprocess.py`
- `preprocess_threads(threads: Sequence[SupportThread], config: Node3Config) -> tuple[list[SupportThread], PreprocessingStats]`:
  1. **Lookback & Future Leakage Filter:** Keep threads where `(config.reference_date - thread.created_at.date()).days <= config.lookback_days` and `thread.created_at.date() <= config.reference_date`.
  2. **Message Cleaning:** Remove messages with `role == "system"`. Deduplicate identical consecutive or redundant customer messages within each thread.
  3. **Message Truncation:** Enforce `max_messages_per_thread` while preserving original IDs and timestamps.
  4. **Cross-Channel Duplicate Detection (Task 4.3):**
     - Group threads by `customer_id`.
     - Evaluate pairwise candidates where `abs((t1.created_at - t2.created_at).total_seconds()) <= dedup_time_window_hours * 3600`.
     - Calculate TF-IDF cosine similarity on `(subject + first_customer_message)` with threshold ≥ 0.82.
     - Calculate normalized subject similarity (`SequenceMatcher.ratio()`) with threshold ≥ 0.75 or check shared key issue phrases.
     - Survivor: Thread with higher customer message token count. Collapsed thread marked with `duplicate_of = survivor.thread_id`.
  5. **Language Detection (Task 4.4):**
     - If `thread.language` is present, check against `config.supported_languages`.
     - Otherwise, detect language from customer message text via deterministic n-gram / stopword scoring.
     - Set `language_status` (`SUPPORTED`, `UNSUPPORTED`, or `UNKNOWN`).
  6. **Customer-level Limits:** Retain up to `max_threads_per_customer` most recent threads.

---

### Component 4: LLM Thread-Level Extraction (Tasks 4.5, 4.7)

#### [NEW] `node3/llm_extractor.py`
- `extract_thread_signals(thread: SupportThread, config: Node3Config, client: LlmClient | None = None) -> ThreadSignals`:
  1. If `thread.language_status == LanguageStatus.UNSUPPORTED`: Mark thread without calling LLM (empty flags, status unsupported).
  2. If thread has 0 customer messages: Emit neutral/unknown sentiment, empty flags, low urgency.
  3. Format prompt with strict schema, controlled vocabulary, and customer message text.
  4. Invoke LLM with `temperature <= 0.2` and structured output.
  5. Validate output against `ThreadSignals`.
  6. If parsing/validation fails, retry once (`llm_max_retries = 1`). If failure persists, quarantine thread: record error in report, increment `n_threads_failed`.
  7. Provide a deterministic offline extractor when `client is None` or `LLM_PROVIDER == "none"` for tests and offline runs.

---

### Component 5: Aggregation, Formulas, & Derivations (Tasks 4.8, 4.9, 4.10)

#### [NEW] `node3/aggregate.py`
- **Recency Calculation:**
  `adjusted_strength = STRENGTH_SCORE[signal_strength] * math.exp(-lambda_val * age_days)`
  where `age_days = (reference_date - thread.created_at.date()).days`, `lambda_val = lambda_persistent` (0.004) for `cancellation_intent` and `renewal_or_contract_concern`, else `lambda_default` (0.015).
- **Flag Aggregation (Task 4.8):**
  Group by `flag_type` across non-collapsed, non-failed threads.
  Compute `recurrence_count` (count of threads where flag appeared).
  Select strongest instance using hierarchy rank + recency-adjusted strength.
  Collect `evidence_message_ids` across occurrences.
- **Evidence Quality Score (Task 4.9):**
  Implements locked formula §3.8.4.
- **Overall Signal Confidence (Task 4.9):**
  Implements locked formula §3.8.5.
- **Customer Derivations (Task 4.10):**
  - `overall_sentiment`: recency-weighted average score; labeled via `[-0.25, 0.25]` thresholds.
  - `urgency_level`: max across non-collapsed threads.
  - `escalation_signal`: `True` if any thread has `urgency == high` OR any flag has `severity == high` and `recurrence >= 2`.
  - `churn_language_detected`: logical OR across non-collapsed threads.
  - `signal_strength`: derived from primary flag (hierarchy-first).
  - `support_data_status`: `"no_data"`, `"limited_data"`, or `"sufficient_data"`.
  - `summary`: deterministic templated sentence or `None`.

---

### Component 6: Node 3 Orchestration & Entry Point (Tasks 4.1, 4.11, 4.12)

#### [NEW] `node3/node.py`
- `run_node3(customers: list[str], support_data: list[SupportThread] | list[dict] | None, config: Node3Config, llm_client: LlmClient | None = None) -> Node3Output`:
  - Handles customer universe: customers with no support data receive `support_data_status = "no_data"`, confidence = 0.0, strength = "none".
  - Runs preprocessing, extraction, and aggregation.
  - Compiles `Node3ProcessingReport` with customer/thread counts, duplicate collapsed counts, LLM calls, warnings, and structured errors.
  - Attaches versioning metadata to all outputs.
- `main(argv: list[str] | None = None) -> int`:
  - CLI subcommand: `churn-survival node3 <threads.json> [--customers <customers.csv>] [--config <version>] [--output <out.json>]`.

#### [MODIFY] `pipeline/main.py`
- Add `"node3"` to `IMPLEMENTED_NODES` and wire `run_node3`.

---

### Component 7: Golden-Set Evaluation Harness (Task 4.13)

#### [NEW] `scripts/eval_node3_golden.py`
- Loads golden annotations (e.g. from `dataset7_ground_truth.json`'s `support_truth`).
- Runs Node 3 on the golden threads.
- Computes Cohen's kappa on `flag_type` (target ≥ 0.70) and `signal_strength` (target ≥ 0.65).
- Computes exact match on `cancellation_intent` and `renewal_or_contract_concern` (target ≥ 0.75).
- Asserts all acceptance criteria are met.

---

### Component 8: Comprehensive Test Suite (Task 4.14)

#### [NEW] `tests/node3/`
- `test_config.py`: configuration loading, limits, validation, unknown versions fail loudly.
- `test_preprocess.py`: lookback windowing, future leakage rejection, system message filtering, message deduplication, truncation limits.
- `test_duplicates.py`: cross-channel duplicate detection, TF-IDF cosine threshold (0.82), subject similarity, token count comparison, survivor selection, count exclusion.
- `test_language.py`: supported/unsupported/unknown detection, quarantine / confidence degradation on unsupported language.
- `test_vocabulary.py`: hierarchy ranks, governance 20% `other` warning.
- `test_llm_extractor.py`: mocked structured output, schema validation, single retry on failure, quarantine on persistent failure, budget enforcement.
- `test_aggregate.py`: recency decay (default vs persistent λ), flag recurrence, temporal span, evidence merging.
- `test_formulas.py`: exact locked formulas for `evidence_quality_score` and `overall_signal_confidence` against known fixtures and boundary cases.
- `test_derivations.py`: sentiment weighting and labeling, urgency max, escalation condition (high urgency vs high severity + recurrence ≥ 2), churn language OR, signal strength hierarchy.
- `test_failure_modes.py`: test all §3.12 failure modes (zero threads, only agent/system messages, invalid LLM, unsupported language, cross-channel duplicate, missing customer_id).
- `test_meta.py`: all 7 version metadata fields present in output.
- `test_flow.py`: end-to-end integration test with synthetic threads and dataset7 sample; CLI invocation exits 0.

---

## Verification Plan

### Automated Tests
1. Run the entire test suite including the new Node 3 tests:
   ```powershell
   .venv\Scripts\pytest -q tests/node3/
   ```
2. Verify test coverage on `node3/` meets or exceeds 90%:
   ```powershell
   .venv\Scripts\pytest --cov=node3 --cov-report=term-missing tests/node3/
   ```
3. Run the full project test suite to verify no regressions in existing nodes:
   ```powershell
   .venv\Scripts\pytest -q
   ```
4. Run linting and type checking:
   ```powershell
   .venv\Scripts\ruff check .
   .venv\Scripts\mypy schemas node3
   ```
5. Run the golden-set evaluation harness:
   ```powershell
   .venv\Scripts\python scripts/eval_node3_golden.py
   ```

### Manual Verification
1. Run Node 3 CLI on the real diagnostic dataset7 support threads:
   ```powershell
   .venv\Scripts\churn-survival node3 data/raw/dataset7_support_threads_messy.json --customers data/raw/dataset7_customers_messy.csv --config v1
   ```
2. Verify the output JSON structure contains `customer_signals`, `thread_signals`, and `processing_report` matching §3.11.
3. Confirm collapsed duplicate count matches the 25 expected cross-channel pairs in `dataset7_ground_truth.json`.

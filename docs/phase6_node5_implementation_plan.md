# Phase 6 Implementation Plan

> **Subject:** Phase 6 = **Node 5 — Client-Facing Risk Report**
> **Status of this document:** planning/analysis only. No production code, tests, or config
> files are created or modified by this plan.
> **Authority:** `architecture.md` (v1.2 + Node 5 spec) wins over `ROADMAP.md`; both were read
> directly. Every requirement below cites its source file and section.

---

## 1. Executive Summary

**Phase 6 is Node 5 — the Client-Facing Risk Report**, not an orchestration or persistence
phase. This is confirmed by:

- `ROADMAP.md:550` — "Phase 6 — Node 5 (Client-Facing Report)", Tasks 6.1–6.12.
- `architecture.md:1946` — §5 "Node 5 — Client-Facing Risk Report", marked *Locked. Ready for
  implementation*.
- `pipeline/main.py:13-15` — `node5` is listed in `NODE_NAMES` but absent from
  `IMPLEMENTED_NODES`.

Node 5 converts the frozen, deterministic `Node4Output` into a presentation report (executive
summary, risk distribution, prioritized accounts, evidence, data-quality, methodology, metadata)
**without performing any new analysis**. It may use an LLM only to *polish explanation text*; a
deterministic template fallback is mandatory, and the LLM can never change risk level, rank,
score, or confidence (`architecture.md §5.2`, §5.15–§5.17, §5.34; `ROADMAP.md` Global Acceptance
Bar #2/#4).

The repository is **partially pre-wired** for Phase 6: contracts (`schemas/node5.py`,
`schemas/enums.py`), config models (`config/models.py: Node5Config`, `ActionRulesConfig`),
loader functions (`config/loader.py: load_node5_config`, `load_action_rules`), and exports
already exist. What does **not** exist: any implementation under `node5/` (only an empty
`node5/__init__.py`), any `config/node5/` or `config/action_rules/` directory, no pipeline
dispatch, and no tests.

**Blocking issues that must be resolved before coding** (see §22/§24):

- **A.** §5.11 needs Node 3 evidence *text/timestamp* but `Node4Output` carries only IDs, and
  §5.3's input contract does not include `Node3Output`.
- **B.** `CustomerReport.rank: int (ge=1)` cannot represent insufficient-data accounts whose
  Node 4 rank is `None`.
- **C.** §5.13's four evidence modes exist as `EvidenceMode` but are not wired into
  `Node5Config` (which has only a boolean).
- **D.** `generated_at` is required but must be deterministic.
- **E.** HTML/PDF have no declared dependency in `pyproject.toml`.

---

## 1b. Resolved Architectural Decisions (locked 2026-09-16, before coding)

These decisions refine — and where noted supersede — the analysis in the sections below. They are
the authoritative resolutions of U1–U11. **Node 4 remains frozen and is the sole source of truth
for risk decisions.**

| Ref | Decision |
|---|---|
| **D-U1** | `run_node5` accepts an **optional `node3_output` used strictly as an evidence *lookup* source**. Node 5 may only resolve evidence references already present in `Node4Output`; it may never introduce evidence Node 4 did not reference. If referenced Node 3 evidence cannot be resolved, Node 5 preserves the Node 4 reference where possible, records a structured warning/error, never invents evidence, and follows `evidence_mode`. |
| **D-API** | The internal API is **strongly typed**: `node4_output: Node4Output` (not `Node4Output \| Mapping`). The CLI deserializes JSON into `Node4Output`; no duck-typed branching inside the node. |
| **D-U3** | `generated_at` is derived **deterministically** from the report's `reference_date`: `datetime.combine(reference_date, time(0, 0), tzinfo=UTC)` (identical to Node 4 D-4 `node4/node.py:222-224`). No injectable `now`, no `datetime.now()` in the report path. |
| **D-U2** | Run-level versions (`node2_model_version`, `node3_signal_version`, `node4_ranking_version`, `node4_threshold_version`, `node4_critical_rules_version`) are resolved by **collecting all non-empty values**: exactly one unique value is used; if multiple unique values exist, Node 5 records a structured error (`MIXED_PROVENANCE`) and deterministically uses the first non-empty value in Node 4 list order (an actual value, never fabricated). |
| **D-U11** | `CustomerReport.rank` becomes `int \| None`. Insufficient-data accounts keep `rank=None`, stay separate, are never presented as Low risk, and never enter the ranked list. |
| **D-U6** | `include_evidence=False` ⇒ **no evidence is emitted regardless of `evidence_mode`**. `include_evidence=True` ⇒ `evidence_mode` controls amount. `evidence_mode` can never bypass `include_evidence=False`. Default `evidence_mode=short_quote`. |
| **D-REC** | Recommendations are produced **only** by the versioned deterministic `ACTION_RULES`; the LLM may explain a selected recommendation but never create one. `action_rules_version` is recorded in report metadata. No reason ⇒ no recommendation. |
| **D-LLM** | The LLM receives only validated Node 4/Node 5 facts and returns structured `{headline, summary, reason_explanations}`. It may never return/alter `risk_level`, `score`, `rank`, `confidence`, `recommendation`, `evidence`, or customer facts. Validation is deterministic Python; **no LLM validates another LLM**. |
| **D-VAL** | Explanation validation is explicit: build a per-account **allowed-facts set** and reject unsupported numbers/percentages/dates/facts/risk-factors/evidence/recommendations, contradictions, and implied different risk levels. On rejection: deterministic template fallback + `llm_failures++`. |
| **D-ORDER** | Node 4 ordering is preserved exactly. `max_accounts_in_summary` is the **maximum number of `priority_accounts` entries**, taken as a deterministic prefix of Node 4 ranked order; it never re-sorts, never affects `risk_distribution` (portfolio-wide, from Node 4 `summary_stats`), and never affects insufficient-data accounts. `n_accounts` = all Node 4 accounts; `n_accounts_reported` = emitted `CustomerReport` objects; truncation records a warning. |
| **D-INSUF** | `include_insufficient_data` only controls whether the insufficient section is emitted; it never changes level/score/rank/confidence/decisions. Insufficient data ≠ Low risk; the section is clearly separated. |
| **D-RENDER** | Rendering is separate from generation. JSON (deterministic) and dependency-free HTML are the core path; both consume the same validated `Node5Output`. **PDF is deferred** (no approved dependency in `pyproject.toml`/§8.11); it must not block the deterministic core. |
| **D-MILESTONES** | Three milestones: **A** deterministic Node 5 (LLM fully disabled), **B** optional LLM explanation + deterministic validator + fallback, **C** adversarial explanation-boundary testing. |
| **D-LANG** | v1 emits English only. `language` is recorded; a non-`en` value records a warning (no localization in v1). |
| **D-U9** | Add `llm_temperature` (default `0.2`, `0..0.2`) and `llm_max_retries` (default `1`) to `Node5Config`, mirroring Node 3. |

**Milestone discipline:** each milestone leaves the repo green; report implemented/changed/tested/
results/issues/freeze-safety; **do not modify Nodes 1–4** (a concrete compatibility issue would be
reported and halted first).

---

## 2. Authoritative Requirements

All requirements derive from `architecture.md §5` (lines 1946–2885) and `ROADMAP.md` Phase 6
(lines 550–633). Node 5 is **Locked**.

### 2.1 Explicitly specified

| # | Requirement | Spec ref |
|---|---|---|
| R1 | Node 5 consumes `node4_output`; `customer_data` optional (name/account/segment/industry/owner only); config carries `report_version`, `prompt_version`, `model_version`, `reference_date`, `include_insufficient_data`, `include_evidence`, `include_recommendations`, `max_accounts_in_summary`, `max_evidence_per_account`, `language`. | §5.3 |
| R2 | Never use raw customer data to calculate risk; fall back to `customer_id` when context missing. | §5.3 |
| R3 | Deterministic pre-generation validation: 12 checks (exists, schema, valid IDs, risk enum ∈ {critical,high,medium,low,insufficient_data}, score∈[0,1], confidence∈[0,1], Critical has qualifying reason, evidence refs valid, summary stats match lists, sequential ranks, no dup in main list, no cross-list membership). On failure: fail safely **and record errors**, never continue silently. | §5.4 |
| R4 | Report structure: Executive Summary → Risk Distribution → Priority Accounts → Detailed Account Analysis → Insufficient Data → Methodology & Data Quality → Report Metadata. | §5.5 |
| R5 | Executive-summary numbers generated deterministically from Node 4 (LLM may polish wording, never numbers). | §5.6 |
| R6 | Risk distribution counts `{critical, high, medium, low, insufficient_data}`; no LLM needed. | §5.7 |
| R7 | Priority accounts in **exactly Node 4 order; never re-sort**; rank copied from Node 4. | §5.8 |
| R8 | Internal `CustomerReport` shape (§5.9), `ReportReason` (§5.10), `ReportEvidence` (§5.11). | §5.9–§5.11 |
| R9 | Node 2 evidence may state values only if present in Node 4; Node 3 quotes must come from Node 3 evidence; **never invent a quotation**. | §5.12 |
| R10 | Evidence presentation modes: disabled / summary only / short quote / full evidence; default short quote/paraphrase; underlying reference always retained internally. | §5.13 |
| R11 | Summary is 2–4 sentences, explanation not analysis. | §5.14 |
| R12 | LLM contract: receives only validated Node 4 info; may not change level/score/rank, invent evidence/facts/causes/risk factors/recommendations, or contradict input; returns structured JSON `{headline, summary, reason_explanations}`; Pydantic-validated; must not return `risk_level`/`rank`/`score`. | §5.15–§5.16 |
| R13 | Deterministic template fallback on LLM fail/timeout/invalid JSON/schema violation/unsupported claims; **LLM failure must never prevent report generation**. | §5.17 |
| R14 | Recommended actions from deterministic versioned `ACTION_RULES` (reason→action); no LLM-authored arbitrary recommendations initially. | §5.18 |
| R15 | Recommendation priority order: cancellation_intent, renewal_or_contract_concern, product_bug_or_outage, poor_support_experience, billing_complaint, usage_drop_related, feature_missing, competitor_mention, other. Never invent a recommendation for a nonexistent reason. | §5.19 |
| R16 | Insufficient-data customers kept separate; "insufficient data ≠ low risk" stated explicitly. | §5.20 |
| R17 | Conflicting evidence (e.g. High quant + positive sentiment) must be preserved, never rewritten as "doing well". | §5.21 |
| R18 | Confidence displayed separately from risk; never phrased as probability of churn; explain low confidence. | §5.22 |
| R19 | Data-quality section built from upstream outputs (sources used, reference date, counts of insufficient/limited). | §5.23 |
| R20 | Methodology section (deterministic, explanatory, audit-friendly). | §5.24 |
| R21 | Report metadata fields: `report_version`, `node2_model_version`, `node3_signal_version`, `node4_ranking_version`, `node4_threshold_version`, `node4_critical_rules_version`, `prompt_version`, `llm_model_version`, `reference_date`, `generated_at`. | §5.25 |
| R22 | Full output contract: `report` + `metadata` + `processing_report` (n_accounts, n_accounts_reported, n_insufficient_data, llm_calls, llm_failures, validation_errors, warnings, errors). | §5.26 |
| R23 | Deterministic pipeline order: validate → normalize → deterministic sections → structured explanations → validate → validate explanations vs source → build report → final consistency → JSON/HTML/PDF. | §5.27 |
| R24 | Explanation validation rejects: different risk level/rank, unsupported numbers/facts/risk factors/evidence/recommendations. Example: Node 4 `cancellation_intent=false` + LLM claims cancellation ⇒ reject. | §5.28 |
| R25 | Pre-publish consistency checks; if any mandatory check fails, **do not publish**. | §5.29 |
| R26 | JSON + HTML + PDF generated from the same validated report object; no separate logic for PDF vs HTML. | §5.30 |
| R27 | Explicit non-goals: no survival/churn/sentiment calc, no flag extraction, no strength/combined-score recalc, no risk/rank changes, no Node 4 override, no external evidence search, no invented facts, confidence ≠ probability, no-data ≠ low risk, LLM output is not source of truth, no unsupported recommendations. | §5.32 |
| R28 | 12 critical invariants (see §21). | §5.34 |
| R29 | Component split: `validation/`, `report/`, `llm/`, `rendering/`, `node.py`. | §5.35 |
| R30 | Implementation order: schemas → validation → deterministic transform → recommendations → LLM → LLM validation → rendering → tests. | §5.36 |
| R31 | LLM is optional explanation polish; deterministic fallback mandatory (project-level). | §9 (Pending Decisions), `ROADMAP.md` bar #4 |
| R32 | Determinism: identical inputs + config versions ⇒ bit-identical output. | `ROADMAP.md` bar #1, `AGENTS.md` hard rule 1 |
| R33 | Every failure is a structured error or warning; nothing silently fails. | `ROADMAP.md` bar #6 |

### 2.2 Implied (strongly supported, not stated verbatim)

| # | Implied requirement | Basis |
|---|---|---|
| I1 | Node 5 must receive Node 3 evidence **text** (or an equivalent pass-through) to satisfy §5.11 `evidence_text`, §5.12 quotations, §5.13 full evidence. | §5.11–§5.13 vs `schemas/node4.py:72-80` (Node 3 evidence ref has only IDs) |
| I2 | `generated_at` must be derived from the declared `reference_date` (or an injectable `now`) to satisfy R32. | §5.25 + `node4/node.py:222-224` D-4 pattern + `node3/node.py:57` `now` injection |
| I3 | `Node5Config` needs an `evidence_mode` (the 4-value `EvidenceMode` enum) rather than only `include_evidence: bool`. | §5.13 vs `config/models.py:218-232`; `schemas/enums.py:141-148` already defines `EvidenceMode` |
| I4 | `action_rules_version` must be recorded in report metadata for auditability (recommendations are config-driven). | §5.18 "versioned for auditability" + §5.25 |
| I5 | Recommendations are keyed by **flag_type** (§5.18/§5.19), but Node 4 `primary_reasons` are `ReasonType`; the flag types are recoverable from `RankedAccount.qualitative.top_flags[*].flag_type` and/or `StructuredReason.evidence_ref["flag_type"]`. | §5.18/§5.19 vs `schemas/node4.py:49-59, 102-117`; `node4/qualitative.py:93-108` |
| I6 | Reuse the existing provider-agnostic `LlmClient` and its `temperature` kwarg for the explainer. | `router/llm_mapper.py:37-88`; `node3/llm_extractor.py:25,464` |
| I7 | `Node5Output`/`CustomerReport` etc. already encode the contract; Phase 6 should extend, not duplicate. | `schemas/node5.py` |
| I8 | CLI subcommand `churn-survival node5 [--node4 <json>] [--config <v>] [--output <json>]` mirroring node4. | `node4/node.py:437-497`, `pipeline/main.py:22-47` |

### 2.3 Under-specified — **RESOLVED in §1b**

All points below are resolved by decisions D-U1…D-U9/D-REC/D-RENDER/D-INSUF/D-ORDER in §1b. The
table is retained as the original analysis/evidence trail.

| # | Open point | Why under-specified |
|---|---|---|
| U1 | Whether Node 5 accepts `Node3Output` (or an evidence map) in addition to `Node4Output`. | §5.3 lists only `node4_output` + `customer_data` + `config`; I1 shows text is otherwise unavailable. |
| U2 | Deterministic rule for run-level `node2_model_version` / `node3_signal_version`. | Not on `Node4Output`; only per-account `evidence_refs`. |
| U3 | `generated_at` value source. | §5.25 requires it; R32 forbids wall-clock. |
| U4 | `max_accounts_in_summary` semantics. | Config field exists; §5.6 example is aggregate counts, not a truncated list. |
| U5 | `language` semantics (only English templates exist). | §5.3 has `language`; no multilingual spec. |
| U6 | `include_insufficient_data` effect (omit section vs mark-only). | §5.20 says keep separate; config default `False` (`config/models.py:227`). |
| U7 | Whether `priority_accounts` includes Low accounts. | §5.26 lists `priority_accounts`; §5.6 example counts all 12 ranked. |
| U8 | HTML/PDF dependency choice; whether PDF ships in Phase 6. | §5.30/§5.35 require renderers; §8.11/pyproject declare no HTML/PDF lib. |
| U9 | LLM temperature / retry policy for the explainer. | §5.15–§5.17 silent; Node 3 uses ≤0.2 + 1 retry. |
| U10 | Exact forbidden-claim validation algorithm. | §5.28 lists categories, no algorithm. |
| U11 | `CustomerReport.rank` for insufficient-data accounts. | §5.9 shows `rank: int`; Node 4 assigns `None` (`node4/ranking.py:48-52`). |

---

## 3. Current Repository State

### 3.1 What exists (verified)

| Area | State |
|---|---|
| Phase 1–5 | Complete; Nodes 1–4 verified/frozen (`AGENTS.md` status; `ROADMAP.md`). |
| `node5/` | Only empty `node5/__init__.py` (git-tracked). No implementation. |
| `schemas/node5.py` | Full contract present (200 lines): `ReportReason`, `ReportFlag`, `Node2ReportReference`, `Node3ReportReference`, `ReportEvidence`, `QuantitativeSummary`, `SupportSummary`, `CustomerReport`, `RiskDistribution`, `DataQualitySection`, `ReportContent`, `ReportMetadata`, `Node5ProcessingReport`, `Node5Output`. |
| `schemas/enums.py` | `ReportRiskLevel` (no `insufficient_data`, line 110), `ReportSeverity` (adds `critical`, line 91), `EvidenceMode` (line 141, currently unused). |
| `config/models.py` | `Node5Config` (line 218), `ActionRulesConfig` (line 235). |
| `config/loader.py` | `load_node5_config` (line 71) → `config/node5/v{version}.json`; `load_action_rules` (line 76) → `config/action_rules/v{version}.json`. |
| `config/node5/`, `config/action_rules/` | **Do not exist.** |
| `pipeline/main.py` | `NODE_NAMES` includes node5; `IMPLEMENTED_NODES` excludes it (line 15); no node5 dispatch. |
| `pyproject.toml` | `node5` in hatch packages (line 69) and coverage source (line 92). No HTML/PDF dependency. |
| `schemas/__init__.py` | All node5 schemas exported (lines 61-76, 109-110). |
| `tests/test_schemas/test_node5.py` | Contract round-trip tests exist. |
| `tests/test_schemas/test_config_models.py` | `Node5Config` defaults + `ActionRulesConfig` round-trip tests exist. |
| `tests/test_pipeline.py` | Two tests **assume node5 is unimplemented** (`test_main_returns_nonzero_with_clear_message` line 38; `test_python_dash_m_exits_nonzero` line 65). These must be updated in Phase 6. |
| `api/`, `orchestration/` | Empty (Phase 7/8). |
| LLM infra | `router/llm_mapper.py: LlmClient` (dataclass, `.complete(prompt, *, temperature=0.2)`, `create_llm_client()`), reused by Node 3. |
| Node 4 output | `Node4Output` (run-level `reference_date`, `summary_stats`, `processing_report`; per-account `rank`, `combined_risk_level`, `combined_score`, `combined_confidence`, `quantitative`, `qualitative`, `primary_reasons`, `evidence_refs`, `meta`). |
| Dataset 7 | Master corpus; golden SHA-256 pinned in `tests/dataset7/test_dataset7.py`; ground truth includes `node5_trap_oracle` (40 customers, 10/trap) and `node4_scenario_oracle`. |
| `docs/` | Only `dataset7_addendum_v1.2.md` and `onboarding.md` before this plan. No Node 5 doc; `architecture.md §5` is authority. |

### 3.2 Document conflicts identified

1. **Input contract vs evidence requirement** — `§5.3` input is `{node4_output, customer_data, config}`; `§5.11/§5.12/§5.13` require Node 3 evidence text. `Node4Output.evidence_refs.node3` (`schemas/node4.py:72-80`) has only `signal_version`, `thread_ids`, `message_ids`. **Conflict → U1.**
2. **Insufficient-data account schema** — `§5.9` `rank: int` vs Node 4 `rank: None` for insufficient accounts (`schemas/node4.py:108`, `node4/ranking.py:51`). **Conflict → U11.**
3. **Evidence modes** — `§5.13` four modes; `Node5Config` only `include_evidence: bool`; `EvidenceMode` enum defined but unused. **Gap → I3.**
4. **Recommendation source** — `§5.18/§5.19` keyed by `flag_type`; Node 4 `primary_reasons` are `ReasonType`. **Gap → I5.**

---

## 4. Phase 6 Purpose

- **Problem solved:** Makes the deterministic, machine-oriented `Node4Output` usable by a human
  client without introducing any new decision logic. It is the communication layer that keeps
  every claim traceable back to Node 2/Node 3 evidence and Node 4 decisions (`§5.0`, §5.2.3).
- **Why it exists:** The system's authority must remain the deterministic model + synthesis;
  presentation must be separated so wording can never alter decisions (`§5.35` "Decision →
  Node 4; Explanation → Node 5 LLM; Presentation → Node 5 renderer").
- **Position:** Last analytical node: `Node 1 → Node 2 → Node 3 → Node 4 → Node 5` (`§5.1`).
- **Node 5 responsibility:** deterministic sections, evidence representation with privacy modes,
  optional structured LLM explanations + validation, deterministic recommended actions, final
  consistency gate, rendering.
- **Previous nodes' responsibility:** Node 1 canonical data; Node 2 survival/risk; Node 3 support
  signals + evidence; Node 4 all synthesis, risk levels, ranks, scores, confidence, reasons,
  evidence references (`§5.0`, `§5.1`).
- **Downstream responsibility (Phase 7/8):** orchestration sequencing and FastAPI serving of
  stored reports; Node 5 itself does not orchestrate or serve.
- **Explicitly must NOT do:** all items in `§5.32` (no survival/churn/sentiment/flag/strength/
  score recalculation; no level/rank change; no Node 4 override; no external evidence; no
  invented facts; confidence ≠ probability; no-data ≠ low risk; LLM output is not source of
  truth; no unsupported recommendations).

---

## 5. Architecture

### 5.1 Pipeline flow (repository-confirmed)

```
Node 1  (canonical records + validation report)      node1/
   ↓
Node 2  (survival model → Node2Output)               node2/
   ↓
Node 3  (support signals → Node3Output)              node3/
   ↓
Node 4  (deterministic synthesis → Node4Output)      node4/   §4.24
   ↓
Phase 6 = Node 5 (client-facing report → Node5Output) node5/  §5.26
   ├── validation/node4_validator.py
   ├── report/{transformer, deterministic_sections, evidence}.py
   ├── llm/{explainer, schemas}.py
   ├── rendering/{json, html, pdf}.py
   └── node.py
```

### 5.2 Boundaries

| Aspect | Determination |
|---|---|
| **Upstream dependency** | `Node4Output` only does analysis. (Evidence text needs Node 3 — U1.) |
| **Downstream dependency** | None in-repo yet; consumed by Phase 7 graph / Phase 8 API. `Node5Output` is the interface. |
| **State boundary** | Stateless pure function; no persistence required (`models/` is for Node 2 artifacts, `§8.5`). |
| **Data boundary** | Reads `Node4Output` (+ optional context/evidence); writes `Node5Output` and rendered bytes. Never mutates upstream. |
| **Deterministic?** | Yes for the report structure/decisions; LLM text is non-authoritative and may vary, but fallback makes output deterministic when LLM is off. |
| **LLM?** | Optional, explanation-only, off by default (`LLM_PROVIDER=none`). |
| **External services?** | Only the configured LLM provider (optional). |
| **Component type** | A standalone node — plain Python functions composed in `node.py`; **not** a LangGraph subgraph (LangGraph is Phase 7, `§6.5`). |
| **Statistical work** | None (no model calls). |

---

## 6. Input Contract

**Recommendation:** Node 5 accepts **typed upstream objects (or mappings coerced to them)** —
reuse `Node4Output` as `Node4Input` — plus an **optional Node 3 evidence source** and optional
`customer_data`.

Rationale: `schemas/node5.py` already defines the output; `Node4Output` already is the
§5.4-validatable schema. §5.36 names a `Node4Input` model, but reusing `Node4Output` avoids a
duplicate contract and keeps the two-indexing/alignment guarantees Node 4 established. A
dedicated *new* input schema is only justified for the evidence pass-through (U1) and
`customer_data`.

Proposed signature (planning only):

```python
run_node5(
    node4_output: Node4Output,                                       # D-API: strongly typed
    config: Node5Config,
    *,
    customer_data: CustomerData | None = None,                       # §5.3 allowed keys only
    node3_output: Node3Output | None = None,                         # D-U1: evidence lookup ONLY
    action_rules: ActionRulesConfig | None = None,
    llm_client: LlmClient | None = None,
) -> Node5Output
```

`node3_output` is an **evidence lookup source only** (D-U1): Node 5 resolves only references
already present in `Node4Output`, never introduces new evidence, and records a structured
warning/error when a referenced item cannot be resolved. `generated_at` is derived from
`reference_date` (D-U3); there is no `now` parameter.

| Input | Source | Type | Req | Validation | Semantics / missing-data |
|---|---|---|---|---|---|
| `node4_output` | Node 4 / CLI `--node4` | `Node4Output` | Yes | `§5.4` 12 checks; whole-object schema failure fails loudly | Source of truth. Missing/invalid ⇒ fail, record validation errors. |
| `customer_data` | optional context file | map keyed by `customer_id` | No | Only allowed keys: name/account/segment/industry/owner (`§5.3`) | Never used for risk; fall back to `customer_id`. Missing entry ⇒ `display_name = customer_id`. |
| `node3_output` | Node 3 / CLI `--node3` | `Node3Output` | No | Validate schema; index by `customer_id` | Enables real evidence text/timestamps (§5.11–§5.13). Absent ⇒ evidence references only, no quotes (never invent). |
| `config` | `config/node5/v{v}.json` | `Node5Config` | Yes | Loader validates; frozen | Drives versions, limits, modes, language. |
| `action_rules` | `config/action_rules/v{v}.json` | `ActionRulesConfig` | No (required iff `include_recommendations`) | Loader validates; version recorded | Deterministic reason→action mapping. |
| `llm_client` | caller / settings | `LlmClient` | No | Provider from env; `none` ⇒ templates | Explanation polish only. |
| `generated_at` | **derived** | `datetime` | — | Always `reference_date` midnight UTC (D-U3) | Not an input; deterministic metadata. |

Malformed-data behavior: whole-object schema failure ⇒ fail loudly (mirrors
`node4/node.py:233-236`); per-account malformed content ⇒ structured validation error, continue
only where `§5.4` permits; `§5.4` says "must not silently continue" — the recommended policy is
**fail the run before generation** when a mandatory check fails, recording all errors (see §13).

---

## 7. Output Contract

Use the existing `Node5Output` (`schemas/node5.py:193-200`). Field-by-field:

### 7.1 `report` (`ReportContent`, §5.26)

| Field | Type | Req | Meaning | Source | Validation | Deterministic |
|---|---|---|---|---|---|---|
| `title` | `str` | Yes | Report title | **New** (constant per `report_version`) | non-empty | Yes |
| `reference_date` | `date` | Yes | Declared cut-off | **Copied** from `Node4Output.reference_date` (must equal `config.reference_date`) | equality | Yes |
| `executive_summary` | `str` | Yes | Management summary | **Derived** deterministically from `summary_stats`; LLM may polish | must contain no unsupported numbers | Yes (template) |
| `risk_distribution` | `RiskDistribution` | Yes | Counts | **Derived** from Node 4 lists | equals `summary_stats` | Yes |
| `priority_accounts` | `list[CustomerReport]` | Yes | Main ranked list | **Transformed** from `ranked_accounts`, exact order | order/rank/level/score/confidence equal Node 4 | Yes |
| `insufficient_data_accounts` | `list[CustomerReport]` | Yes | Separated | **Transformed** from `insufficient_data_accounts` | disjoint from main; rank handling (U11) | Yes |
| `data_quality.notes` | `list[str]` | Yes | Upstream sources/counts | **Derived** from Node 4 + upstream metadata | no invented claims | Yes |
| `methodology` | `str` | Yes | Methodology text | **New** (versioned constant) | non-empty | Yes |

### 7.2 `metadata` (`ReportMetadata`, §5.25)

| Field | Source | Notes |
|---|---|---|
| `report_version` | config | **Copied** |
| `node2_model_version` | Node 4 `evidence_refs.node2.model_version` | **Derived** run-level (U2) |
| `node3_signal_version` | Node 4 `evidence_refs.node3.signal_version` | **Derived** run-level (U2) |
| `node4_ranking_version` | Node 4 `meta.ranking_version` | **Copied** |
| `node4_threshold_version` | Node 4 `meta.threshold_version` | **Copied** |
| `node4_critical_rules_version` | Node 4 `meta.critical_rules_version` | **Copied** |
| `prompt_version` | config | **Copied** |
| `llm_model_version` | injected client `.model` or None | **New / recorded** (mirror `router/llm_mapper.py:333` F-4 provenance fix) |
| `reference_date` | Node 4 / config | **Copied** |
| `generated_at` | `now` or reference-date midnight | **New** (U3) |
| *(proposed)* `action_rules_version` | action rules config | **New** — justified by I4 |
| *(proposed)* `evidence_mode` | config | **New** — justified by I3 |

### 7.3 `processing_report` (`Node5ProcessingReport`, §5.26)

`n_accounts`, `n_accounts_reported`, `n_insufficient_data`, `llm_calls`, `llm_failures`,
`validation_errors`, `warnings`, `errors` — **New** counters/records derived from the run.

### 7.4 `CustomerReport` (`§5.9`)

| Field | Source |
|---|---|
| `customer_id` | Copied |
| `display_name` | Derived from `customer_data` else `customer_id` |
| `rank` | Copied (U11 for insufficient) |
| `risk_level` (`ReportRiskLevel`) | Copied from `combined_risk_level` (never `insufficient_data`) |
| `combined_score` | Copied verbatim (no rounding — see Node 4 F-1) |
| `combined_confidence` | Copied verbatim |
| `headline` / `summary` | New (LLM or template) |
| `primary_reasons` (`ReportReason`) | Transformed from `primary_reasons` |
| `quantitative_summary` | Transformed from `quantitative` |
| `support_summary` | Transformed from `qualitative` |
| `evidence` (`ReportEvidence`) | New wrapper around upstream refs (text only if Node 3 supplied) |
| `data_quality_notes` | Derived (limited/no data, low confidence) |
| `recommended_action` | Derived from `ACTION_RULES` gated by config |

**Fields deliberately NOT added:** no new scores, no probability-of-churn, no re-derived levels,
no customer facts beyond allowed context.

---

## 8. Data Flow

Stages (mapping §5.27):

1. **Ingest & coerce** — validate `node4_output`, `customer_data`, `node3_output`, config, action
   rules. Output: typed objects. Errors: schema failure ⇒ fail loudly.
2. **Node 4 validation** — run the 12 §5.4 checks (`validation/node4_validator.py`). Output:
   validation result (errors list). Failure ⇒ record all errors and do not generate (run fails
   safely).
3. **Normalize/index** — build deterministic lookup maps: account by `customer_id`, Node 3 signal
   by `customer_id`, thread/message→evidence text. Preserve list order. No sets used for output.
4. **Deterministic sections** — executive summary, risk distribution, data quality, methodology
   (`report/deterministic_sections.py`). Inputs: `summary_stats`, lists, upstream metadata.
   Output: strings/objects.
5. **Per-account transform** — `report/transformer.py`: build `CustomerReport` skeleton with
   copied decision fields, reasons, quantitative/support summaries, data-quality notes; order
   strictly preserved.
6. **Evidence build** — `report/evidence.py`: wrap Node 2/Node 3 refs per `ReportEvidence`; apply
   `evidence_mode` + `max_evidence_per_account`; quotes only from Node 3.
7. **Recommendations** — deterministic `ACTION_RULES` selection by §5.19 priority, gated by
   `include_recommendations`.
8. **Explanation** — if LLM enabled, `llm/explainer.py` gets per-customer validated structured
   payload and returns `{headline, summary, reason_explanations}`; Pydantic-validated.
   Deterministic template otherwise.
9. **Explanation validation** — `§5.28`: reject contradictions/unsupported claims; on rejection
   count failure and fall back to template.
10. **Assemble report** — build `ReportContent` + `ReportMetadata` + `Node5ProcessingReport`.
11. **Final consistency gate** — §5.29 checks; any mandatory failure ⇒ do not publish (raise/fail,
    record errors).
12. **Render** — JSON/HTML/PDF from the same object (`rendering/`).

Preservation guarantees: customer identity (verbatim IDs), ordering (Node 4 list order, never
re-sorted), rank/level/score/confidence (copied), evidence (referenced, quotes only from source),
reasons (mapped from `ReasonType`), reference date (copied), upstream provenance (versions),
warnings/errors (surfaced). Nothing is discarded without a recorded reason.

---

## 9. Core Logic

Components (per §5.35 split), each with responsibility / algorithm / edge cases:

1. **`node5/validation/node4_validator.py`** — `validate_node4_output()`, `validate_customer()`,
   `validate_evidence_refs()`, `validate_summary_stats()` per §5.4. Checks: object exists;
   Pydantic schema; non-empty valid `customer_id`; `combined_risk_level` ∈ enum; `0≤score≤1`;
   `0≤confidence≤1`; every Critical has ≥1 `ReasonType` starting `critical_`; `evidence_refs`
   structure valid; `summary_stats` equals counts over lists; main-list ranks sequential `1..n`;
   no duplicate in main; main ∩ insufficient = ∅. **Note:** §5.4 also says
   `combined_risk_level` may be `insufficient_data`, so this check spans the union of lists.
   Edge: empty main list is valid (all insufficient).
2. **`node5/report/deterministic_sections.py`** — `build_executive_summary()`,
   `build_risk_distribution()`, `build_data_quality_section()`, `build_methodology_section()`.
   Numbers strictly from Node 4. Executive summary uses `max_accounts_in_summary` (U4) for the
   "priority accounts" count phrase; distribution uses exact counts. Risk distribution must
   satisfy the §5.7 example shape.
3. **`node5/report/transformer.py`** — `build_customer_report(account, ...)`. Copies decision
   fields; maps `StructuredReason` → `ReportReason` (statement from a versioned
   `ReasonType→text` table, similar to `node4/reasons.py:_REASON_TEXT` but client-facing and
   without the debug suffix); maps `quantitative` → `QuantitativeSummary`; maps `qualitative`
   → `SupportSummary`; sets `data_quality_notes` for limited/no data and low confidence; keeps
   strict Node 4 order. Edge: `risk_score`/`survival_prob_90d` None; `top_flags` may be empty;
   `customer_state=not_enough_data`.
4. **`node5/report/evidence.py`** — evidence mapping + privacy modes. `EvidenceMode.DISABLED` ⇒
   empty list; `SUMMARY_ONLY` ⇒ description only, no quote/text; `SHORT_QUOTE` (default) ⇒
   truncated quote from Node 3 `strongest_evidence.text` (never invented); `FULL_EVIDENCE` ⇒
   full text. `max_evidence_per_account` caps the list deterministically (first N in Node 3 flag
   order). Node 2 evidence: `node2_reference = {model_version, feature_ref}` only when values
   exist in Node 4.
5. **`node5/report/recommendations.py`** — deterministic `ACTION_RULES`. Selection: collect
   candidate flag types from `qualitative.top_flags` (filtered to non-`positive_feedback`) and/or
   reason `evidence_ref["flag_type"]`; order by §5.19 priority; choose first rule present;
   `include_recommendations=False` ⇒ `None`; no reason ⇒ `None` (never invent). Missing key in
   `rules` ⇒ `None` + warning.
6. **`node5/report/reason_text.py`** (or constants in transformer) — versioned `ReasonType →
   client statement` table (must correspond to an actual Node 4 reason; §5.10). Include
   conflict/agreement phrasing (§5.21).
7. **`node5/llm/explainer.py` + `llm/schemas.py`** — LLM call + Pydantic
   `{headline, summary, reason_explanations}`. Build per-customer payload containing only
   validated Node 4 fields + approved evidence; temperature low; on failure/invalid ⇒ template
   (R13).
8. **`node5/report/explanation_validator.py`** — §5.28 checks: no different level/rank/score; no
   numeric token unsupported by source; no new `flag_type`/risk factor; no evidence not in refs;
   no recommendation not from `ACTION_RULES`; contradiction check (e.g. claims cancellation when
   no cancellation flag). On failure ⇒ fallback + `llm_failures++`.
9. **`node5/report/consistency.py`** — §5.29 pre-publish checks (level/rank/score/confidence
   equal; counts equal; Critical supported by critical rules; insufficient separated; evidence
   refs valid; versions recorded). Mandatory failure ⇒ do not publish.
10. **`node5/node.py`** — `run_node5(...)` orchestration + CLI `main()`. Deterministic template
    fallback when no LLM client. Builds `Node5Output`.

**Extracted deterministic rules/tables to encode:** §5.6 summary template; §5.7 distribution;
§5.18 `ACTION_RULES`; §5.19 priority list; §5.23/§5.24 section templates; §5.10 statement table;
§5.9/§5.11 structures; §5.29 checks. No formula is assigned to the LLM.

---

## 10. LLM Boundary

- **Does Phase 6 use an LLM?** Yes, **optional explanation polish only** (`§5.15`, `ROADMAP.md`
  Task 6.5, Global bar #4). Default off (`LLM_PROVIDER=none`); deterministic fallback mandatory.
- **LLM is responsible for:** generating `headline`, `summary`, and `reason_explanations` text
  from supplied structured facts.
- **LLM is NOT allowed to decide/determine:** risk level, rank, score, confidence, reasons
  existence, evidence existence, recommendations, counts, the customer universe. It must not
  return `risk_level`/`rank`/`score` (`§5.16`); such fields if returned are ignored/rejected.
- **Input context:** only the validated per-customer structured info (customer_id, risk_level,
  rank, score, confidence, primary_reasons, quantitative, qualitative, approved evidence) — never
  other customers' data, never raw upstream files (`§5.15`, §5.2.2).
- **Structured output schema:** `{"headline": str, "summary": str, "reason_explanations":
  list[str]}`, Pydantic-validated (`§5.16`).
- **Validation:** `§5.28` explanation validation (contradictions, unsupported numbers/facts/
  factors/evidence/recommendations).
- **Retry:** mirror Node 3 (one retry; `config/models.py:138` `llm_max_retries=1`); `Node5Config`
  currently lacks it → propose `llm_max_retries` + `llm_temperature ≤ 0.2` (U9) or reuse fixed
  values.
- **Failure behavior:** any failure (timeout/invalid JSON/schema/unsupported claim) ⇒ template
  fallback; `llm_calls`/`llm_failures` counted; **report still generated** (R13).
- **Token/output constraints:** reuse `LlmClient.complete` (`router/llm_mapper.py:51`);
  clamp/limit output (e.g. summary length) — under-specified (U9/U10).
- **Prompt/version management:** `prompt_version` in config and metadata (`§5.25`); prompt text
  versioned in code.
- **Model/version recording:** `llm_model_version` from `client.model` (F-4 pattern).
- **Hallucination prevention:** constrained input, structured output, no-invention prompt rules
  (§5.15), explanation validation (§5.28), template fallback.
- **Evidence requirements:** quotes only from Node 3 evidence; otherwise paraphrase/summary mode.

---

## 11. Configuration

Use the existing `Node5Config` (`config/models.py:218-232`) at `config/node5/v1.json`, and
`ActionRulesConfig` (`config/models.py:235`) at `config/action_rules/v1.json` (loader paths
already defined, `config/loader.py:71-78`).

| Name | Type | Default | Range | Purpose | Behavior-changing | Version bump |
|---|---|---|---|---|---|---|
| `report_version` | str | none (required) | semver-ish | Report semantics | Yes | self |
| `prompt_version` | str | none (required) | — | Explainer prompt | Yes (if LLM) | self |
| `model_version` | str\|None | None | — | Configured LLM model | Yes (LLM) | recorded |
| `reference_date` | date | required | ISO | Declared cut-off | Yes | recorded |
| `include_insufficient_data` | bool | `False` | — | Section visibility (U6) | Yes | config |
| `include_evidence` | bool | `True` | — | Evidence on/off | Yes | config |
| `include_recommendations` | bool | `True` | — | Recommendations gate | Yes | config |
| `max_accounts_in_summary` | int | required | ≥1 | Summary count limit (U4) | Yes | config |
| `max_evidence_per_account` | int | required | ≥0 | Evidence cap | Yes | config |
| `language` | str | `"en"` | U5 | Report language | Yes | config |
| *(added)* `evidence_mode` | `EvidenceMode` | `SHORT_QUOTE` | 4 modes | §5.13 (D-U6) | Yes | config |
| *(added)* `llm_temperature` | float | `0.2` | `0..0.2` | LLM decoding (D-U9) | Yes (LLM) | config |
| *(added)* `llm_max_retries` | int | `1` | `≥0` | LLM retries (D-U9) | Yes (LLM) | config |
| `action_rules_version` | str | required | — | Action rule set | Yes | self |
| `action_rules` | `dict[str,str]` | required | flag types | Reason→action | Yes | config |

- **Do not invent config values beyond §5.3/§5.13/§5.18 plus the justified
  `evidence_mode`/`action_rules_version`/LLM knobs.**
- **`max_accounts_in_summary` (D-ORDER):** max number of `priority_accounts` emitted, a
  deterministic prefix of Node 4 ranked order; never re-sorts; never changes
  `risk_distribution` (portfolio-wide, from `summary_stats`) or insufficient accounts.
- **`include_evidence` / `evidence_mode` (D-U6):** `include_evidence=False` ⇒ no evidence at all,
  regardless of mode; otherwise the mode selects `disabled`/`summary_only`/`short_quote`/
  `full_evidence`.
- Note: `Node5Config` is frozen; changing it (I3) requires adding fields with defaults so
  existing tests (`tests/test_schemas/test_config_models.py:85`) keep passing.
- `config/node5/` and `config/action_rules/` directories do not exist and must be created in
  Phase 6.

---

## 12. Schemas

### 12.1 Reuse as-is

`ReportReason`, `ReportFlag`, `Node2ReportReference`, `Node3ReportReference`, `ReportEvidence`,
`QuantitativeSummary`, `SupportSummary`, `RiskDistribution`, `DataQualitySection`,
`ReportContent`, `ReportMetadata`, `Node5ProcessingReport`, `Node5Output`. All `extra="forbid"`
(`schemas/node5.py`).

### 12.2 Required extensions (justified)

1. **`CustomerReport.rank` must allow `None`** for insufficient-data accounts (`rank: int |
   None`), backed by `node4/ranking.py:51`. Without this, `Node5Output` cannot represent §5.20.
   (Alternatively, split insufficient into a distinct schema; but §5.26 types both lists as
   `list[CustomerReport]`, so relaxing `rank` is the minimal change.) **D-U11 — locked.**
2. **`Node5Config.evidence_mode: EvidenceMode = SHORT_QUOTE`** (I3) — the enum already exists
   (`schemas/enums.py:141`). **D-U6 — locked.**
3. **`ReportMetadata.action_rules_version: str | None = None`** (I4). **D-REC — locked.**
4. **`Node5Config.llm_temperature: float = 0.2 (0..0.2)`** and
   **`Node5Config.llm_max_retries: int = 1`** — mirror Node 3. **D-U9 — locked.**

### 12.3 No new schema for input

Reuse `Node4Output` as the validatable `Node4Input` (§5.36) — it already encodes §5.4 checks. A
separate model would duplicate the frozen Node 4 contract.

### 12.4 Compatibility

- `ReportRiskLevel` correctly omits `insufficient_data` (`schemas/enums.py:110`).
- `ReportSeverity` correctly includes `critical` (`schemas/enums.py:91`).
- `ReportReason.reason_type: ReasonType` matches Node 4 reasons.
- `ReportReason.evidence_ref: str | dict[str, Any]` accommodates Node 4's polymorphic refs.
- `SupportSummary.signal_strength: OverallSignalStrength` matches `QualitativeInfo.signal_strength`.
- Any schema edit requires updating `tests/test_schemas/test_node5.py` and keeping `mypy schemas`
  strict-clean.

---

## 13. Error Handling

Policies derive from `§5.4` (fail safely, record errors), `§5.17` (LLM failure never blocks),
`§5.29` (do not publish on mandatory inconsistency), and `AGENTS.md` hard rule 6.

| Situation | Policy | Basis |
|---|---|---|
| Missing `node4_output` | Fail immediately, non-zero CLI, structured error | §5.4 |
| Invalid Node 4 schema (whole) | Fail immediately (`ValidationError`), record error count | §5.4; `node4/node.py:233` |
| Individual account fails a §5.4 check | Fail the run before generation, record all validation errors; **do not** partially publish | §5.4, §5.29 |
| Duplicate customer IDs in main list | Validation error ⇒ do not publish | §5.4 |
| Customer in both lists | Validation error ⇒ do not publish | §5.4 |
| Missing evidence for a claim | Omit that evidence; never invent; add data-quality note | §5.12, §5.32 |
| Node 3 text unavailable (no `node3_output`) | Degrade to reference-only/summary evidence; no quotes | I1 |
| Inconsistent upstream (e.g. `no_data` + flags) | Node 4 already flags `INCONSISTENT_SUPPORT_STATUS`; Node 5 surfaces in warnings, does not re-decide | §4.26; `node4/node.py:292` |
| Missing/invalid config | Fail immediately via loader | `config/loader.py:30-38` |
| Missing action rules when needed | Warning + `None` recommendation (unless `include_recommendations` and missing file ⇒ fail) | §5.18/§5.19 |
| LLM failure/timeout/invalid JSON/schema violation | Deterministic template fallback; `llm_failures++`; continue | §5.17 |
| LLM unsupported claim / contradiction | Reject explanation → template fallback; count failure | §5.28, §5.17 |
| Partial processing | Not allowed for mandatory checks; either publish fully-validated or fail | §5.4, §5.29 |
| Serialization failure | Fail loudly; do not write a partial artifact | `AGENTS.md` rule 6 |

---

## 14. Determinism

- **Collection iteration:** never iterate a `set`/dict for output ordering; use Node 4 list order
  and explicitly ordered collections. Use dicts for lookup only.
- **Customer ordering:** preserved exactly from `ranked_accounts` / `insufficient_data_accounts`;
  **never re-sort** (`§5.8`).
- **Ranking ties:** Node 4 already provides the total order (`node4/ranking.py:25-36`); Node 5
  copies ranks verbatim.
- **Timestamps (D-U3):** `generated_at = datetime.combine(reference_date, time(0,0),
  tzinfo=UTC)` — exactly. Never `datetime.now()` anywhere in the report path; there is no
  injectable `now` (mirrors Node 4 D-4 `node4/node.py:222-224`).
- **Randomness:** none.
- **External APIs:** only optional LLM; off by default; output non-authoritative; fallback
  deterministic.
- **LLM nondeterminism:** bounded by validation + fallback; LLM text is excluded from any
  decision or count.
- **Floating point:** copy Node 4 floats verbatim; never re-round scores/confidence (Node 4 F-1
  lesson, `node4/test_output.py:195-206`).
- **Exact tie-breaking required:** none new — Node 5 must not introduce ordering; if selecting
  the "top" evidence/flags it uses the order already present in Node 4 (`top_flags` is pre-ordered
  by `node4/qualitative.py:93-108`).
- **Invariant (§5.34 #12):** "Same validated inputs and versions produce the same deterministic
  report structure." Test with two runs (LLM off) and assert `model_dump(mode="json")` equality.

---

## 15. Security

- **Authentication/authorization:** none inside Node 5; no API in Phase 6. Authorization is never
  delegated to the LLM (project rule). Phase 8 FastAPI will own authn/authz.
- **Secrets:** `LLM_API_KEY` from env/`.env`; never logged, never embedded in output (`AGENTS.md`
  rule 8; `.env.example`).
- **Sensitive data:** customer names, segments, account owners, and support message text are
  personal data. Render only allowed context (`customer_data` allowlist:
  name/account/segment/industry/owner).
- **Customer isolation:** build per-customer LLM payloads containing only that customer's data;
  never send the full report or other customers' evidence.
- **Data leakage risks:** LLM may leak raw internal fields if overfed — restrict to validated
  Node 4 fields + approved evidence; use `EvidenceMode` to control text exposure.
- **Evidence exposure:** `§5.13` modes gate quotes; internal object retains full reference;
  client report respects `evidence_mode`/`max_evidence_per_account`.
- **Masked identifiers:** `display_name` falls back to `customer_id`; no fabricated PII.
- **Logging:** structured logs may include counts/versions; must not log raw message text or keys.

---

## 16. Versioning and Provenance

Recorded in `ReportMetadata` (`§5.25`, `schemas/node5.py:161-175`):

| Version | Source | Justification |
|---|---|---|
| `report_version` | config | report semantics |
| `node2_model_version` | Node 4 `evidence_refs.node2.model_version` | quantitative provenance |
| `node3_signal_version` | Node 4 `evidence_refs.node3.signal_version` (D-3 composite) | qualitative provenance |
| `node4_ranking_version` / `node4_threshold_version` / `node4_critical_rules_version` | Node 4 `meta` | ranking/threshold/rules provenance |
| `prompt_version` | config | explainer prompt |
| `llm_model_version` | client `.model` | LLM provenance |
| `reference_date` | Node 4 / config | cut-off |
| `generated_at` | derived | reproducibility |
| *(proposed)* `action_rules_version` | action rules config | recommendation auditability (I4) |

Run-level derivation rule (D-U2): for each of `node2_model_version`, `node3_signal_version`,
`node4_ranking_version`, `node4_threshold_version`, `node4_critical_rules_version`, collect all
**non-empty** values scanning `ranked_accounts` then `insufficient_data_accounts` in Node 4 order.
If exactly one unique value exists, use it; if none, use `""`; if multiple unique values exist,
record a structured `MIXED_PROVENANCE` error and deterministically use the first non-empty value
in Node 4 order (an actual value — never fabricated). `node3_signal_version` is a D-3 composite
and may legitimately differ per customer, so mixed values are expected to be surfaced rather than
hidden.

Behavior-changing inputs (config fields, prompt, action rules, algorithm/reason-text table) are
all versioned or recorded so downstream consumers can identify semantics.

---

## 17. File Structure

Proposed (derive from `§5.35` + `node3/`/`node4/` conventions):

```
node5/
├── __init__.py                       # exists; add public exports
├── node.py                           # run_node5() + CLI main()
├── validation/
│   ├── __init__.py
│   └── node4_validator.py            # §5.4 checks (do NOT put report building here)
├── report/
│   ├── __init__.py
│   ├── transformer.py                # account → CustomerReport (no analysis)
│   ├── deterministic_sections.py     # exec summary, distribution, data quality, methodology
│   ├── evidence.py                   # §5.11–§5.13 evidence + privacy modes
│   ├── recommendations.py            # §5.18/§5.19 action selection
│   ├── reason_text.py                # ReasonType → client statement table
│   ├── explanation_validator.py      # §5.28 (if not in llm/)
│   └── consistency.py                # §5.29 pre-publish gate
├── llm/
│   ├── __init__.py
│   ├── explainer.py                  # §5.15/§5.16 LLM call
│   └── schemas.py                    # LLM output Pydantic model
└── rendering/
    ├── __init__.py
    ├── json.py                       # deterministic JSON (core)
    └── html.py                       # dependency-free HTML from same object (core)
    # pdf.py deferred (D-RENDER): no approved dependency; must not block the core

config/
├── node5/v1.json                     # Node5Config (generic)
├── node5/vdataset7.json              # Node5Config (dataset7 deployment)
└── action_rules/v1.json              # ActionRulesConfig (§5.18/§5.19)

tests/node5/
├── __init__.py
├── conftest.py                       # builders: Node4Output fixtures, mock LlmClient
├── test_validation.py
├── test_deterministic_sections.py
├── test_transformer.py
├── test_evidence.py
├── test_recommendations.py
├── test_explanation_validation.py
├── test_consistency.py
├── test_invariants.py                # §5.34 twelve
├── test_failure_modes.py             # §5.33
├── test_rendering.py
├── test_output.py                    # §5.26/CLI
└── test_e2e.py                       # dataset7
```

**What must NOT go here:** no survival/statistical computation, no Node 4 re-classification, no
re-ranking, no customer-data risk inference, no wall-clock calls, no direct Node 3 extraction.

**Existing files to modify during implementation (not now):**

- `pipeline/main.py` — add node5 to `IMPLEMENTED_NODES` and dispatch (`pipeline/main.py:15,39-43`).
- `schemas/node5.py` — `rank: int | None`; `ReportMetadata` additions.
- `config/models.py` — `Node5Config.evidence_mode` (+ optional `llm_temperature`/`llm_max_retries`).
- `schemas/__init__.py` — export any new symbols (already exports most).
- `tests/test_pipeline.py` — update the two "node5 unimplemented" tests.
- `AGENTS.md` / `ROADMAP.md` — mark Phase 6 complete at the end.
- `pyproject.toml` — only if HTML/PDF dependency is approved.
- `schemas/enums.py` — no change needed (`EvidenceMode` exists).

---

## 18. Testing Strategy

Follow `tests/node4/` conventions (pytest, builder factories in `conftest.py`, `fresh_settings`
fixture).

| Category | Tests | Requirement |
|---|---|---|
| Schema | `rank` optional; `ReportReason`/`ReportEvidence` round-trips; `ReportMetadata` completeness | §5.9–§5.11, §5.25 |
| Config | `Node5Config` defaults incl. `evidence_mode`; `ActionRulesConfig` round-trip; frozen | §5.3, §5.18 |
| Unit — validation | one test per §5.4 check (positive + negative) | §5.4 |
| Unit — sections | exec-summary numbers equal `summary_stats`; distribution counts; data-quality/methodology non-empty | §5.6–§5.8, §5.23–§5.24 |
| Unit — transformer | level/rank/score/confidence copied verbatim; reasons mapped; order preserved | §5.8–§5.10, §5.34 |
| Unit — evidence | 4 modes; `max_evidence_per_account`; no invented quote; Node 2 values only if present | §5.11–§5.13 |
| Unit — recommendations | §5.19 priority; gating; no reason ⇒ None; missing key ⇒ None | §5.18–§5.19 |
| Unit — LLM | mocked `LlmClient`: valid JSON; forbidden fields (`risk_level`/`rank`/`score`) rejected; invalid JSON; timeout | §5.15–§5.16 |
| Unit — explanation validation | contradiction (cancellation false), different level/rank/score, unsupported number/fact/evidence/recommendation | §5.28 |
| Unit — consistency | each §5.29 check + mandatory failure blocks publish | §5.29 |
| Integration | full `run_node5` on a synthetic Node4Output with mixed levels | §5.26 |
| Failure | LLM off/fail/timeout/invalid → template report; missing Node 3 evidence; missing customer name; malformed Node 4 | §5.17, §5.32, §5.33 |
| Determinism | two runs (LLM off) ⇒ identical dump; `generated_at` stable | §5.34 #12 |
| Invariants | all 12 §5.34 as automated tests | §5.34 |
| Security/isolation | LLM payload excludes other customers and disallowed context; evidence modes gate text | §5.13, §15 |
| E2E | Dataset 7 pipeline → Node 5 (see §19) | `ROADMAP.md` Task 6.12 |
| CLI | `node5 --node4 <json> [--config] [--output]`; usage exit 2; missing file exit 1; output written | `node4/node.py:448-497` pattern |
| Rendering | JSON deterministic; HTML contains same numbers; PDF (if shipped) same object | §5.30 |

**Adversarial cases to include:** LLM claims cancellation without a cancellation flag; LLM
inflates low→high; LLM adds a recommendation not in `ACTION_RULES`; LLM cites a thread/message
not in refs; duplicate ID; rank gap; summary stats mismatch; insufficient customer in main list;
`positive_feedback` used as a risk driver; `customer_data` attempting to influence risk.

---

## 19. Dataset 7 Strategy

- **Inputs available:** `data/raw/dataset7_customers_messy.csv`,
  `data/raw/dataset7_support_threads_messy.json`,
  `data/ground_truth/dataset7_ground_truth.json`; golden SHA-256 pinned
  (`tests/dataset7/test_dataset7.py:38-42`).
- **Scale:** 4,680 accepted accounts reach Node 4; Node 4 smoke distribution (AGENTS.md)
  `critical=245 high=0 medium=11 low=4074 insufficient=350`.
- **Oracle:** `node5_trap_oracle` — 40 trap customers (10 per trap) with `trap_kind`,
  `truth_risk_level`, `forbidden_claims`, `narrative`, `expected_risk_level` (= Node 4 oracle).
  `docs/dataset7_addendum_v1.2.md:409-422`; validator checks 50/51.
- **Justified assertions:**
  - `report.risk_distribution` == Node 4 `summary_stats` counts.
  - `priority_accounts` order == Node 4 `ranked_accounts` order (no re-sort).
  - Each `CustomerReport.risk_level/score/confidence` == its Node 4 account.
  - `insufficient_data_accounts` == Node 4 insufficient list; disjoint from main.
  - Every Critical report retains ≥1 critical reason.
  - `reference_date` == `2026-08-15`; `generated_at` deterministic.
  - Two runs (LLM off) bit-identical.
  - Trap oracle: for each trap `customer_id`, `expected_risk_level` == Node 4 level == report
    level; and generated deterministic text does not assert a `forbidden_claims` term.
- **Invariants only (not exact assertions):** exact human wording; LLM-produced text
  (nondeterministic; only checked for absence of forbidden claims when LLM is exercised via
  mock); `action_rules` phrasing.
- **Oracle limitations (honest):** `node4_scenario_oracle` uses different display thresholds and
  a non-contract reason vocabulary, so it is **not** authoritative for Node 5
  (`tests/node4/test_e2e.py:3-6` documents this). The trap oracle's `forbidden_claims` are
  semantic and should be checked against deterministic fields or a mocked LLM, not treated as a
  string-perfect oracle. With `LLM_PROVIDER=none`, there is no free-form LLM prose, so trap
  checks reduce to confirming Node 5 does not alter Node 4 decisions.

---

## 20. Implementation Sequence

Adapted from `§5.36` and `ROADMAP.md` Tasks 6.1–6.12. Each step ends in a green, testable state.

**Milestones (D-MILESTONES):** Steps 2–6, 11–13 = **Milestone A** (deterministic Node 5, LLM
disabled). Steps 7–10 = **Milestone B** (optional LLM explanation + deterministic validator +
fallback). **Milestone C** = adversarial explanation-boundary tests (§18). At the end of each
milestone, report: (1) what was implemented, (2) files changed, (3) tests added, (4) results,
(5) architectural issues found, (6) freeze-safety.

1. **Resolve U1–U11** → recorded in §1b. *(No files.)*
2. **Schemas** — extend `schemas/node5.py` (`rank: int|None`, metadata fields) + schema tests.
   *(§5.9/§5.25/§5.26)*
3. **Config** — add `evidence_mode` (+ optional LLM knobs) to `Node5Config`; create
   `config/node5/v1.json` and `config/action_rules/v1.json`; config tests. *(§5.3/§5.13/§5.18)*
4. **Pure helpers** — `report/reason_text.py`, `recommendations.py` (unit tests).
   *(§5.10/§5.18–§5.19)*
5. **Validation** — `validation/node4_validator.py` (one test per check). *(§5.4)*
6. **Deterministic transform** — `deterministic_sections.py`, `transformer.py`, `evidence.py`; a
   complete report without AI. *(§5.6–§5.13, §5.23–§5.24)*
7. **Recommendations wiring** — integrate into `run_node5`, gated. *(§5.18)*
8. **LLM explainer** — `llm/explainer.py` + `llm/schemas.py`, mocked tests; deterministic
   fallback. *(§5.15–§5.17)*
9. **Explanation validation** — `explanation_validator.py`; contradiction/unsupported tests.
   *(§5.28)*
10. **Consistency gate** — `consistency.py`; mandatory-failure blocks publish. *(§5.29)*
11. **Pipeline + CLI** — modify `pipeline/main.py`; `node5/node.py` CLI; update
    `tests/test_pipeline.py`. *(§5.26/§5.27)*
12. **Rendering** — JSON first, then HTML, then PDF per U8. *(§5.30)*
13. **Dataset 7 E2E** — `tests/node5/test_e2e.py` + trap-oracle assertions. *(§5.33/§5.34)*
14. **Full regression** — `pytest`; coverage ≥90% on `node5/`. *(Global bar)*
15. **Static checks** — `ruff check .`, `mypy schemas`. *(Task 0.7)*
16. **Independent adversarial QA** + update `AGENTS.md`/`ROADMAP.md`, then freeze.

At each step: invariant tests pass; no Node 4/3/2/1 modification; deterministic reruns.

---

## 21. Acceptance Gates

Phase 6 is complete only when **all** hold:

1. All `node5/` tests pass; full `pytest` suite green (existing 763 + new).
2. `node5/` coverage ≥ 90% (matches prior nodes).
3. `ruff check .` clean.
4. `mypy schemas` clean (strict) — and new `node5/` typecheck if practical.
5. Dataset 7 Node1→Node2→Node3→Node4→Node5 E2E passes with the §19 assertions.
6. **All 12 §5.34 invariants** pass as automated tests.
7. Determinism: two identical runs (LLM off) produce bit-identical `Node5Output`.
8. Schema validation: `Node5Output` round-trips; `rank=None` insufficient accounts validate.
9. Failure handling: every §13 policy has a test (LLM failures, missing evidence, malformed
   upstream, missing config).
10. Security/isolation: evidence modes gate text; LLM payload isolation test.
11. CLI: `churn-survival node5 --node4 <json> --config <v> --output <json>` exits 0; `--help`/bad
    args exit 2; missing file exit 1.
12. Final consistency gate (§5.29) demonstrably blocks a deliberately inconsistent report.
13. No model responses or wall-clock affect any decision/rank/score/confidence.
14. `AGENTS.md` status + `ROADMAP.md` Phase 6 checkboxes updated.

---

## 22. Risks and Ambiguities

**Confirmed technical risks**

- R1. **Evidence text gap (U1)** — without `Node3Output` (or an evidence bundle),
  `Node3ReportReference.evidence_text` cannot be populated and §5.12 quotes are impossible.
  Highest-impact blocker.
- R2. **`rank=None` insufficient accounts (U11)** — current `CustomerReport` cannot represent
  them; must relax the schema.
- R3. **`generated_at` determinism (U3)** — naive implementation breaks the bit-identical rule.
- R4. **HTML/PDF dependencies (U8)** — none declared; adding heavy deps risks scope creep and
  install failures.
- R5. **Recommendation source mapping (I5)** — `ReasonType` ≠ `flag_type`; must document the
  extraction rule.

**Architectural risks**

- A1. §5.3 does not permit a Node 3 input yet §5.11–§5.13 require its content — the plan's
  recommended optional-input approach is an architectural extension that must be approved.
- A2. `ReportMetadata` omits `action_rules_version` though recommendations are config-driven (I4).

**Specification ambiguities (must resolve):** U2–U7, U9, U10.

**Schema mismatches:** R2 (`rank`); `Node5Config` lacking `evidence_mode`; `ReportMetadata`
lacking `action_rules_version`.

**Downstream compatibility risks:** Phase 7/8 will consume `Node5Output`; adding metadata fields
now is safer than later. PDF/HTML renderer signatures should accept the validated report object
only.

**Assumptions:** `Node4Output` is stable/frozen (per `AGENTS.md`); `LlmClient` remains the shared
client; `reference_date` is authoritative.

**Decisions required before coding:** all of §24.

**Uncertainty is not hidden:** U1–U11 are genuinely under-specified by the repository and are not
converted into requirements above.

---

## 23. Final Implementation Checklist

- [ ] U1–U11 resolved and recorded.
- [ ] `schemas/node5.py`: `rank: int | None`; `ReportMetadata.action_rules_version`
      (+ `evidence_mode`).
- [ ] `config/models.py`: `Node5Config.evidence_mode` (+ LLM knobs if chosen).
- [ ] `config/node5/v1.json` and `config/action_rules/v1.json` created and validated.
- [ ] `validation/node4_validator.py` — 12 §5.4 checks.
- [ ] `report/deterministic_sections.py` — exec summary, distribution, data quality, methodology.
- [ ] `report/transformer.py` — CustomerReport with copied decision fields, order preserved.
- [ ] `report/evidence.py` — 4 modes, cap, no invented quotes.
- [ ] `report/recommendations.py` — §5.19 priority, gated.
- [ ] `report/reason_text.py` — client statements matching Node 4 reasons.
- [ ] `llm/explainer.py` + `llm/schemas.py` — structured output, fallback.
- [ ] `report/explanation_validator.py` — §5.28.
- [ ] `report/consistency.py` — §5.29 do-not-publish gate.
- [ ] `node5/node.py` — `run_node5` + CLI.
- [ ] `rendering/{json,html,pdf}.py` per U8.
- [ ] `pipeline/main.py` dispatch + `tests/test_pipeline.py` updated.
- [ ] `tests/node5/` full suite (schema, config, unit, integration, failure, determinism,
      invariants, security, E2E, CLI).
- [ ] `pytest`, `ruff`, `mypy schemas` green; dataset7 E2E green.
- [ ] `AGENTS.md` + `ROADMAP.md` updated; Node 5 frozen after QA.

---

## 24. Questions That Must Be Resolved Before Coding

**All resolved in §1b (2026-09-16).** No blocking questions remain; coding proceeds by milestone.

1. **Evidence source (U1)** → **D-U1:** optional `node3_output` as evidence lookup only.
2. **`generated_at` (U3)** → **D-U3:** derived from `reference_date` midnight UTC; no `now`.
3. **Insufficient rank (U11)** → **D-U11:** `rank: int | None`; insufficient stays separate.
4. **Evidence mode (I3)** → **D-U6:** `evidence_mode` added; `include_evidence=False` always wins.
5. **Run-level provenance (U2)** → **D-U2:** collect non-empty; unique ⇒ use; mixed ⇒ error + first
   non-empty, deterministic, never fabricated. Node 4 remains unmodified.
6. **Rendering deps (U8)** → **D-RENDER:** JSON + dependency-free HTML core; PDF deferred.
7. **`max_accounts_in_summary` (U4)** → **D-ORDER:** prefix cap on `priority_accounts`; never
   re-sorts; portfolio counts unaffected.
8. **`include_insufficient_data` (U6)** → **D-INSUF:** controls section emission only.
9. **`priority_accounts` scope (U7)** → all ranked accounts (including Low), subject to the
   prefix cap.
10. **`language` (U5)** → **D-LANG:** English-only in v1; recorded; non-`en` ⇒ warning.
11. **LLM knobs (U9)** → **D-U9:** add `llm_temperature` (≤0.2) + `llm_max_retries`.
12. **Forbidden-claim validation (U10)** → **D-VAL:** deterministic allowed-facts validator
    (numbers/dates/facts/flags/evidence/recommendations/contradictions/implied levels).
13. **`action_rules_version` (I4)** → **D-REC:** recorded in `ReportMetadata`.
14. **Reason vocabulary** → Node 5 owns its client-facing `ReasonType→statement` table; it does
    **not** reuse Node 4's debug strings (`node4/reasons.py:_REASON_TEXT`), which include a
    `source/severity` suffix.

---

## 25. Implementation Status (2026-09-16)

**Milestones A, B, C — complete.**

- **Milestone A (deterministic Node 5, LLM disabled):** `node5/validation/node4_validator.py`,
  `node5/report/{deterministic_sections,transformer,evidence,recommendations,reason_text,consistency}.py`,
  `node5/rendering/{json,html}.py`, `node5/node.py`; versioned `config/node5/v1.json` +
  `vdataset7.json`, `config/action_rules/v1.json`. Schema/config changes:
  `CustomerReport.rank: int | None`; `ReportMetadata.action_rules_version`; `ReportRiskLevel`
  gained `insufficient_data` (required so §5.26's `insufficient_data_accounts:
  list[CustomerReport]` can represent the first-class state); `Node5Config.evidence_mode`,
  `llm_temperature`, `llm_max_retries`.
- **Milestone B (optional LLM):** `node5/llm/{schemas,explainer}.py` +
  `node5/report/explanation_validator.py`; strict `{headline, summary, reason_explanations}`
  output (extra fields rejected), deterministic allowed-facts validation, mandatory template
  fallback, `llm_calls`/`llm_failures` counters, `llm_model_version` recorded.
- **Milestone C (adversarial):** `tests/node5/test_adversarial.py` + `test_explanation_validation.py`
  cover level inflation, invented cancellation/facts/percentages/timestamps/evidence,
  unsupported recommendations, probability mis-statements, and Node 4 contradictions.
- **Tests:** 104 Node 5 tests; full suite 868 passed, 1 live-LLM skipped; `node5/` coverage 93%;
  `ruff check .` clean; `mypy schemas` clean; Dataset 7 E2E green (distribution/order/decisions
  preserved; 350 insufficient separated; trap cohort preserves Node 4 levels).
- **Discovered issue (reported, not fixed):** the dataset-7 `node5_trap_oracle` disagrees with
  the real Node 4 output for 20/40 trap customers (traps 001 usage_drop, 002 billing_complaint)
  — an oracle/Node 4 discrepancy (the oracle uses non-contract thresholds/vocabulary, as
  `tests/node4/test_e2e.py` already documents). Node 5 must surface it, not fix it; the E2E test
  asserts Node 5 == Node 4 for the trap cohort.
- **Freeze-safety:** Nodes 1–4 untouched; deterministic reruns (LLM off) bit-identical; pipeline
  and CLI dispatch for `node5` wired. Safe to freeze Node 5 after external adversarial QA.

### 25.1 Adversarial-QA remediation (2026-09-16)

An independent audit found nine issues; all are addressed with implementation changes and
regression tests (no Node 4 changes, no validation weakening):

- **F-1 (CRITICAL)** — Node 3 evidence is keyed by `(customer_id, message_id)` and thread
  ownership is verified; foreign references are never published and produce structured
  `EVIDENCE_CUSTOMER_MISMATCH` / `EVIDENCE_THREAD_MISMATCH` errors. Covered by
  `tests/node5/test_evidence_binding.py` (8 audit cases).
- **F-2 (HIGH)** — explanation validation rebuilt around an explicit **allowed-facts model**
  (`node5/report/explanation_validator.py`): unsupported recommendations (ACTION_RULES-derived
  action vocabulary), risk factors (flag→concept mapping), material customer facts, numeric
  claims (percentages always rejected, spelled-out numbers checked), dates (date-like
  expressions only), and risk-level claims are all rejected. The capitalization/sentence-position
  heuristic was removed.
- **F-3 (MEDIUM)** — required provenance must be non-empty and unambiguous; mixed upstream
  versions block publication (`DoNotPublishError`); `action_rules_version` is required when
  recommendations are enabled (`node5/report/consistency.py`).
- **F-4 (LOW)** — malformed `top_flags` raise structured `INVALID_TOP_FLAG` errors (no silent
  drops, no `KeyError`).
- **F-5 (LOW)** — duplicate insufficient-data IDs and cross-list membership are rejected.
- **F-6 (LOW)** — "may" as a verb is no longer treated as a date (only date-like expressions are
  validated).
- **F-7 (LOW)** — HTML emits account-level Quantitative signals and Support signals from the
  validated object; PDF remains intentionally deferred and documented in
  `node5/rendering/__init__.py`.
- **F-8 (INFO)** — display names are bounded (≤120 chars), control chars stripped, whitespace
  collapsed, falling back to `customer_id`.
- **F-9 (INFO)** — LLM `reason_explanations` are validated but not surfaced (the locked §5.9
  `CustomerReport` has no field for them; documented in `explanation_validator.py`); the dead
  `Node3EvidenceIndex.signals` field was removed.

**Result:** 899 tests passing, 1 live-LLM skipped; `node5/` coverage 94%; `ruff check .` clean;
`mypy schemas` clean; Dataset 7 E2E green.

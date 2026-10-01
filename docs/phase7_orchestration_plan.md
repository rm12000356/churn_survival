# Phase 7 — Orchestration (plain Python, no LangGraph)

**Status:** complete (2026-09-27)
**Architecture refs:** §0.1 (router), §1.2 (Node 1 batch stop), §1.6 (mapping
report), §6.5 ("Orchestration … may use LangGraph **or equivalent**"), §8.3.
**Roadmap:** Phase 7, Tasks 7.1–7.3.

## Scope

Wrap the frozen Nodes 1–5 with a deterministic sequencer that:

1. routes the raw input (§0.1) without ever calling an LLM;
2. gates any unknown shape behind a **human mapping confirmation** (§1.6);
3. sequences Node 1 → Node 2 → Node 3 → Node 4 → Node 5 with explicit stop
   conditions;
4. returns a resumable, round-trippable run state.

All statistical work stays in the node packages. This layer only routes, gates,
sequences, and reports.

## Decision record

### D-O1 — Plain Python, not LangGraph
Architecture §6.5 explicitly allows "LangGraph **or equivalent**". This phase
implements the equivalent: a small explicit state machine
(`orchestration/graph.py`) over the node functions. No graph framework is
imported anywhere; the `orchestration` optional dependency stay in
`pyproject.toml` is unused by the code. Rationale: the flow is linear with two
decision points, and determinism/debuggability matter more than a graph runtime.

### D-O2 — Resume re-enters at routing (no hot mid-pipeline resume)
`PipelineState` / `PipelineResult` round-trip to JSON
(`to_dict` / `from_dict` / `save` / `load`). A run that stops with
`STOPPED_NEEDS_MAPPING` persists the routing fingerprint and (optionally) the
draft `MappingReport`. Resuming re-enters the pipeline at the **routing** stage
and re-runs Node 1–5: Node 1 is deterministic and idempotent, so re-entry is
equivalent to a hypothetical mid-pipeline resume and is cross-request safe.

**There is no hot-resume-from-mid-pipeline capability.** A web frontend is a
request/response system: the "confirm this mapping" request persists the mapping
and calls `resume_pipeline` (or `run_pipeline` again); it does not continue a
blocked process. The `MappingGate` callback is a *same-process* convenience for
the CLI, not a blocking primitive for the frontend.

### D-O3 — The confirmation gate is the only path to persistence
`orchestration/mapping.py` defines the `MappingGate` protocol; the graph never
auto-confirms and never fabricates a decision. A gate returns the human-approved
`MappingReport` (or `None`); only then does the graph persist it through
`persist_confirmed_mapping` → `router.llm_mapper.confirm_and_persist` (so the
audited transformation whitelist and strict validation apply unchanged).

The **LLM-assisted draft** is produced *before* the gate by the existing Node 1
mapping workflow (`node1.node.run_mapping_workflow`, CLI
`churn-survival map --llm`) or manually (`build_draft_mapping_report`,
`churn-survival map`). Phase 8 may wrap drafting in an API endpoint, but it must
feed the resulting `MappingReport` **into** this gate — Phase 8 must never call
`confirm_and_persist` directly, bypassing human review.

### D-O4 — Partial state is retained on failure
`run_pipeline` wraps each stage in `try/except`. An unexpected node exception is
recorded as a structured error (`NODE_EXCEPTION` with `stage`, exception type,
message), the status becomes `FAILED`, and the `PipelineResult` is returned with
every already-completed node output intact. Clean stops retain progress too
(`STOPPED_VALIDATION` keeps `node1_output`; `STOPPED_NEEDS_MAPPING` keeps the
fingerprint + routing decision). Node exceptions never propagate out of
`run_pipeline`; only malformed argv raises to the CLI (exit 2). This makes a
failure debuggable live, not just in tests.

### D-O5 — Artifact retention
`run_pipeline` defaults to `persist_artifact=False`; the CLI opts in with
`--persist-artifact`. Model versions are content-addressed
(`derive_dataset_version` hashes the encoded design matrix; `derive_model_version`
hashes that plus config versions/penalizer/ties/horizons, 16 hex chars), so:

- re-running the **same** input overwrites the same `models/<model_version>/`
  directory (repeats do not grow the store);
- **distinct** inputs create distinct directories; collision risk across a demo's
  handful of datasets is negligible.

Retention/GC (max-count or TTL pruning under `models/`) is deferred to Phase 8.
The standalone `node2` CLI keeps its existing persist-by-default behavior.

### D-O6 — Stop conditions and the Node 3 baseline
| Condition | Status | Downstream |
|---|---|---|
| No high-confidence adapter + no confirmed mapping | `STOPPED_NEEDS_MAPPING` | none |
| Node 1 batch `FAILED` (empty accepted) | `STOPPED_VALIDATION` | none (§1.2) |
| Unexpected node exception | `FAILED` (structured) | up to the failure |
| Reached Node 5 | `COMPLETED` | all |

Support inputs are optional, but Node 3 **always runs** for the canonical
universe: with no threads it emits an honest `no_data` baseline. This is required
because Node 5 refuses to publish a report with an empty provenance version
(`metadata.node3_signal_version`); skipping Node 3 entirely would leave that
empty. Node 4 synthesis is then effectively quantitative-only.

## Surfaces

- **Library:** `run_pipeline(...)`, `resume_pipeline(...)`,
  `PipelineResult`, `CallbackMappingGate`.
- **CLI:** `churn-survival run <raw-file> [--node1..--node5 <v>] [--threads
  <json>] [--sources <x,gmail|mock>] [--sources-config <v>] [--identity-map <v>]
  [--source-mode mock|live] [--mapping <draft.json> --confirm-mapping]
  [--action-rules <v>] [--persist-artifact] [--output <result.json>]`.

Exit codes: `0` completed, `1` stopped/failed, `2` usage error.

## Verification

- `tests/orchestration/` — routing, sequencing, stop conditions + partial state,
  mapping confirmation (decline/approve/reuse/round-trip resume), determinism,
  CLI, and a dataset-7 full-pipeline E2E.
- `ruff check .`, `mypy schemas`, and the full `pytest` suite stay green.

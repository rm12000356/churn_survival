# Phase 9 — Production Hardening Plan

**Status:** implemented (2026-09-27).

Implements ROADMAP Phase 9 (Tasks 9.1 observability, 9.2 golden sets, 9.3
reproducibility audit). This document locks the decisions taken while building
it; `architecture.md` remains the authority for any contract or formula.

## Locked decisions

- **D-H1 — No OpenTelemetry.** Architecture §8.8 permits OpenTelemetry "only if
  already in use". It is not, so Phase 9 uses structured logging (`structlog`,
  already a core dependency) only. No tracing/metrics SDK is added.
- **D-H2 — Boundary observability.** Structured logs are emitted at the
  orchestration boundary, the API boundary, and each node's *CLI entry point*.
  Frozen node internals (Node 1–5 decision logic) are not modified: the
  orchestrator already receives each node's versions/counts in its output
  contract, so it can log them without instrumenting frozen code.
- **D-H3 — No wall-clock as output.** structlog timestamps and request durations
  are operational telemetry. They never enter `PipelineState`, node outputs, or
  the report. `reference_date`-derived values remain the only deterministic time
  inputs.
- **D-H4 — No secrets or untrusted content logged.** Events carry ids, versions,
  counts, statuses and durations — never API keys, raw rows, thread/message text,
  evidence text, or request bodies/headers. `HTTP` logs `method`/`path`/`status`/
  `duration_ms` only.
- **D-H5 — CI provisions dataset 7 by generation.** The corpus under `data/` is
  gitignored (runtime directory, §8.5). CI runs `scripts/generate_dataset7.py`
  (fixed master seed) and then verifies the pinned golden SHA-256 through
  `tests/dataset7` + `scripts/validate_dataset7.py`. This keeps ~18 MB out of git
  and exercises generator determinism on the CI platform. *Contingency:* if
  cross-platform drift ever appears, force-add the corpus (documented fallback,
  not the default).
- **D-H6 — Golden gate uses the offline deterministic extractor.** The §3.10
  acceptance bars are enforced by `pytest` against the `LLM_PROVIDER=none`
  keyword extractor (no network, deterministic). The live-LLM harness
  (`scripts/eval_node3_golden.py --live`) stays a manual, pre-prompt/model-change
  step.
- **D-H7 — The audit re-supplies Node 3 support inputs.** The Phase 8 persisted
  run stores only `support_digest`, not the raw support/external messages
  (privacy). The audit accepts `--threads/--sources/--identity-map` and verifies
  the recomputed digest; runs whose support inputs are not supplied are reported
  `MISSING_SUPPORT_INPUTS` and **skipped** (a skip is not a failure).
- **D-H8 — Serving invariants preserved.** API read endpoints still never
  recompute; Phase 9 changes are additive (logging + offline tooling). No risk
  level, score, rank, confidence, or evidence is altered.
- **D-H9 — Routing pre-flight before re-run.** `run_id` includes the resolved
  `routing_identity` (adapter + version) computed against the *current* adapter
  registry (Phase 8, D-P1). Before the expensive full re-run, the audit performs
  the same cheap, decision-free pass the trigger path uses
  (`build_adapters` → `route_input` → `routing_identity`) and compares it with
  `state.json`'s recorded `routing_identity`. A mismatch short-circuits to
  `MAPPING_CHANGED` (skip) *before* re-running and byte-diffing. This prevents a
  changed mapping registry from being reported as a wall of generic byte
  mismatches (`node1.json`…`report.html`) that would be indistinguishable from a
  genuine non-determinism regression.

## Task 9.1 — Observability

- `logging_setup.py`: `configure_logging()` (JSON or console via `LOG_FORMAT`),
  `get_logger(node, **version_fields)`, plus
  `bind_run_context(run_id, reference_date)` / `clear_run_context()` built on
  `structlog.contextvars`.
- `orchestration/graph.py`: bind the run context once `run_id` exists (cleared in
  `finally`); emit `run_started`, a `stage_finished` per node carrying the node,
  its config version, returned version fields (`mapping_version`,
  `model_version`, `signal_version`) and counts, and a terminal
  `run_completed` / `run_stopped` / `run_failed`.
- `api/`: `configure_logging()` at startup; request middleware logging
  `http_request`; `execute_run` emits `run_enqueued` / `run_completed` /
  `run_failed`.
- Node CLIs (`node1..node5/node.py:main`): `configure_logging()` + one
  `node_run_completed` summary event. No business-logic change.

Verification: `tests/test_observability.py` asserts a sample line carries
`event`, `level`, `timestamp`, `node`, `run_id` and version fields, and that a
sentinel API key / support-message text never appear.

## Task 9.2 — Golden sets

- `scripts/eval_node3_golden.py` exposes a pure
  `evaluate_golden(config, *, live=False, client=None) -> GoldenResult`; the CLI
  delegates. Bars: κ(`flag_type`) ≥ 0.70, κ(`signal_strength`) ≥ 0.65,
  cancellation/renewal exact-match ≥ 0.75.
- `tests/golden/test_node3_golden.py` (`@pytest.mark.golden`) runs the offline
  extractor on dataset 7 and enforces the bars, so the CI `pytest` run **is** the
  gate. Dataset 7 is a proxy for the architecture's 150–300 double-annotated real
  threads; re-evaluate on every major prompt/model change (live harness).
- `.github/workflows/ci.yml`: `ruff check .` → `mypy schemas` →
  `python scripts/generate_dataset7.py` (`D-H5`) → `python scripts/validate_dataset7.py`
  → `pytest`.

## Task 9.3 — Reproducibility audit

`scripts/audit_reproducibility.py` audits persisted runs (`--run-id` or `--all`):

1. Load `runs/<run_id>/state.json` (raw path, config versions, `reference_date`,
   `support_digest`, `routing_identity`).
2. **Routing pre-flight (D-H9):** rebuild adapters, re-route, compare
   `routing_identity`. Mismatch → `MAPPING_CHANGED` (skip). A `None` recorded
   identity (legacy) skips the pre-flight and notes it.
3. **Support gate (D-H7):** recompute `compute_support_digest`; mismatch →
   `MISSING_SUPPORT_INPUTS` (skip).
4. **Full re-run:** `run_pipeline` with the same versions/`config_dir`/
   `reference_date`.
5. **Checks:** recomputed `run_id` equality; byte-equality of `node1..node5.json`
   and `report.html`. Mismatch → `FAIL` listing the differing paths.

Exit `0` = all attempted PASS (skips reported), `1` = any FAIL, `2` = usage.

**Documented residual:** an in-place mapping mutation that keeps the same adapter
name/version leaves `routing_identity` unchanged, so the pre-flight cannot
attribute it; the byte-diff then reports `FAIL` (the run genuinely no longer
reproduces). This is intentional and not silently absorbed.

Verification: `tests/e2e/test_reproducibility_audit.py` — PASS, `MAPPING_CHANGED`
(with a `run_pipeline` short-circuit spy), `MISSING_SUPPORT_INPUTS`, and a
corrupted-persisted-node `FAIL`.

## Out of scope

OpenTelemetry/tracing, committing the dataset 7 corpus, persisting Node 3 support
inputs, changes to any node's decision logic, PDF rendering.

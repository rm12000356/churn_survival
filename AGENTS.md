# AGENTS.md

Project context for AI agents working in this repository. Read this first.

## What this project is

Churn Survival Analysis System — a 5-node Python pipeline that turns heterogeneous customer data into a ranked, explainable customer-risk report:

- **Node 1** — canonical schema + adapters (deterministic preferred, LLM only for one-time mapping)
- **Node 2** — survival model (CoxPH + Kaplan-Meier fallback)
- **Node 3** — qualitative support signal extraction + evidence
- **Node 4** — deterministic synthesis → ranked account list
- **Node 5** — client-facing risk report

## Source of truth

- `architecture.md` — **the authority.** Every contract, schema, rule, formula, and non-negotiable. ~3000 lines. Do not rescan fully; read the specific section(s) the task references.
- `ROADMAP.md` — the implementation sequence. Every task cites its architecture sections. Start here to know what to build next.

If anything appears to conflict, `architecture.md` wins.

## Current status

- **Phase 1 (Shared Schema Foundation) — complete.** Tasks 1.1–1.4 done:
  `schemas/` Pydantic contracts (canonical, validation, mapping, node2–5) with
  centralized StrEnum vocabulary (`schemas/enums.py`), strict models
  (`extra="forbid"` except open containers), frozen versioned config models +
  JSON/YAML loader (`config/models.py`, `config/loader.py`; real config files
  deferred to Phase 5), and schema strictness tests (91 passing; ruff + `mypy schemas` clean).
- Next work is **ROADMAP Phase 2 — Node 1** (Router + Adapters + Canonicalization).
- Keep this status section accurate; update it as phases complete.

## Planned repo layout (ROADMAP Task 0.2 / architecture §8.10)

```
churn_survival/
├── adapters/               # deterministic adapters + base class
├── schemas/                # Pydantic models (canonical, reports, outputs)
├── router/                 # signature detection + routing logic
├── node1/ ... node5/       # one package per node
├── models/                 # saved model artifacts + mapping configs
├── orchestration/          # LangGraph graphs
├── api/                    # FastAPI routes (later)
├── tests/
├── config/                 # versioned config files (thresholds, vocab, prompts)
├── data/raw|processed/
└── pyproject.toml
```

## Hard rules (never violate)

1. **Determinism** — same inputs + same config versions → bit-identical output. `reference_date` is a declared cut-off, never "today" at runtime.
2. **LLM has zero authority** — over any score, rank, risk level, confidence, or evidence. Optional, explanation-polish only (Node 5) or thread-level extraction (Node 3). Deterministic fallback is mandatory.
3. **The combined score alone can never produce Critical.** Explicit critical rules only (Node 4 §4.10).
4. **Statistical work stays in plain Python functions** (lifelines, pandas, numpy). LangGraph is for orchestration only.
5. **Pydantic contracts are strict.** `extra_features` and `key_themes` are the only open dicts; never auto-feed them to a model.
6. **The system must be able to say "I don't know"** — `INSUFFICIENT_DATA`, `no_data`, `not_enough_data`, `explanation: null` are first-class states, not failures.
7. **No future leakage** — `observation_end <= reference_date` for every record.
8. **No secrets in git** — `.env` is never committed; use `.env.example` + pydantic-settings.

## Commands

The repo root *is* the package root (flat layout per architecture §8.10), so module
paths are top-level (e.g. `schemas.canonical`, `config.settings`, `pipeline.main`).
Run everything from the repo root with the venv active (`uv run ...`):

```bash
pip install -e ".[dev]"
pytest                       # run tests
pytest --cov                 # coverage
ruff check .                 # lint
mypy schemas                 # typecheck (strict for schemas, see pyproject overrides)
churn-survival <node>        # pipeline entry (skeleton; exits non-zero while nodes are stubs)
```

## How to work here

1. Check `ROADMAP.md` for the current phase/task and its stated `Verification`.
2. Read only the architecture sections the task cites.
3. Implement, then prove the task's verification bar (usually fixture-based tests).
4. Update this file's status + ROADMAP checkboxes when a phase completes.

"""Pipeline orchestration (ROADMAP Phase 7, architecture §6.5).

A deterministic, plain-Python state machine — the architecture permits
"LangGraph **or equivalent**"; this is the equivalent, with no graph framework
dependency. It sequences the frozen nodes and enforces explicit stop conditions:

    route -> [human mapping gate] -> Node 1 -> Node 2 -> [Node 3] -> Node 4 -> Node 5

Guarantees:

- **Routing is LLM-free.** An unmatched shape stops for human confirmation; the
  graph never auto-translates and never auto-confirms a mapping.
- **Node 1 total validation failure short-circuits** (no Node 2/3/4/5).
- **Partial state is retained.** A node exception is recorded as a structured
  error and the run returns ``FAILED`` with every completed node output intact.
- **Deterministic.** ``reference_date`` and the run timestamp derive from the
  declared dataset cut-off, never wall-clock time.
- **Resume re-enters at routing.** There is no hot mid-pipeline resume; Node 1 is
  deterministic and idempotent, so re-entry is equivalent and cross-request safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from config.loader import (
    load_action_rules,
    load_node1_config,
    load_node2_config,
    load_node3_config,
    load_node4_config,
    load_node5_config,
)
from config.models import (
    ActionRulesConfig,
    IdentityMappingConfig,
    Node1Config,
    Node2Config,
    Node3Config,
    Node3SourcesConfig,
    Node4Config,
    Node5Config,
)
from config.settings import Settings, get_settings
from orchestration.identity import compute_run_id, compute_support_digest, sha256_file
from orchestration.mapping import MappingGate, persist_confirmed_mapping
from orchestration.routing import (
    build_adapters,
    route_input,
    routing_identity,
    routing_summary,
)
from orchestration.state import (
    PipelineResult,
    PipelineStage,
    PipelineState,
    PipelineStatus,
)
from schemas.enums import ValidationStatus
from schemas.mapping import MappingReport
from schemas.node3 import SupportThread
from schemas.validation import Node1Output

__all__ = ["resume_pipeline", "run_pipeline"]


def _record_exception(state: PipelineState, stage: PipelineStage, exc: Exception) -> None:
    state.errors.append(
        {
            "code": "NODE_EXCEPTION",
            "stage": stage.value,
            "type": type(exc).__name__,
            "message": str(exc),
        }
    )
    state.warnings.append(f"{stage.value} failed: {exc}")
    state.status = PipelineStatus.FAILED


def _resolve_action_rules(
    action_rules: ActionRulesConfig | None, state: PipelineState
) -> ActionRulesConfig | None:
    if action_rules is not None:
        return action_rules
    try:
        return load_action_rules("1")
    except Exception as exc:  # noqa: BLE001 - recommendations are optional
        state.warnings.append(f"ACTION_RULES unavailable: {exc}")
        return None


def run_pipeline(
    raw_path: str | Path,
    *,
    node1_config: Node1Config | None = None,
    node2_config: Node2Config | None = None,
    node3_config: Node3Config | None = None,
    node4_config: Node4Config | None = None,
    node5_config: Node5Config | None = None,
    node1_version: str = "1",
    node2_version: str = "1",
    node3_version: str = "1",
    node4_version: str = "1",
    node5_version: str = "1",
    support_data: Sequence[SupportThread | dict[str, object]] | None = None,
    external_threads: Sequence[SupportThread | dict[str, object]] | None = None,
    sources_config: Node3SourcesConfig | None = None,
    identity_mapping: IdentityMappingConfig | None = None,
    customer_data: Mapping[str, Any] | None = None,
    action_rules: ActionRulesConfig | None = None,
    llm_client: Any | None = None,
    vocabulary: Any | None = None,
    settings: Settings | None = None,
    mapping_gate: MappingGate | None = None,
    mapping_report: MappingReport | None = None,
    config_dir: str | Path | None = None,
    persist_artifact: bool = False,
    reference_date: date | None = None,
    now: datetime | None = None,
) -> PipelineResult:
    """Run the full pipeline on ``raw_path``; always returns a ``PipelineResult``.

    ``config_dir`` scopes confirmed-mapping adapter loading and persistence (it
    defaults to the settings ``CONFIG_DIR``). ``persist_artifact`` is opt-in so a
    demo instance does not accumulate model artifacts by default.
    """
    from node1.node import run_node1
    from node2.artifact import save_artifact
    from node2.node import fit_model, run_node2, score_to_output
    from node3.node import run_node3, run_node3_from_sources
    from node4.node import run_node4
    from node5.node import run_node5

    settings = settings or get_settings()
    reference_date = reference_date or settings.REFERENCE_DATE
    now = now or datetime.combine(reference_date, time(0, 0), tzinfo=UTC)

    node1_config = node1_config or load_node1_config(node1_version)
    node2_config = node2_config or load_node2_config(node2_version)
    node3_config = node3_config or load_node3_config(node3_version)
    node4_config = node4_config or load_node4_config(node4_version)
    node5_config = node5_config or load_node5_config(node5_version)

    state = PipelineState(
        raw_path=str(raw_path),
        reference_date=reference_date,
        config_versions={
            "node1": node1_version,
            "node2": node2_version,
            "node3": node3_version,
            "node4": node4_version,
            "node5": node5_version,
        },
    )
    result = PipelineResult(state)

    # --- Run identity inputs (D-P1) ------------------------------------------
    # The mapping registry is input configuration, so the versions of every
    # config that can change the output — including source/identity configs and
    # the action-rules set — are part of a run's identity.
    if sources_config is not None:
        state.config_versions["sources_config"] = sources_config.sources_version
    if identity_mapping is not None:
        state.config_versions["identity_mapping"] = identity_mapping.mapping_version
    action_rules = _resolve_action_rules(action_rules, state)
    if action_rules is not None:
        state.config_versions["action_rules"] = action_rules.action_rules_version
    state.support_digest = compute_support_digest(
        support_data=support_data,
        external_threads=external_threads,
        sources_config_version=(
            sources_config.sources_version if sources_config is not None else None
        ),
        identity_mapping_version=(
            identity_mapping.mapping_version if identity_mapping is not None else None
        ),
    )

    # --- Routing (architecture §0.1) -----------------------------------------
    # Fingerprint + route only: decision-free, no node is executed to form the
    # run identity.
    state.stage = PipelineStage.ROUTING
    try:
        state.raw_digest = sha256_file(raw_path)
        adapters = build_adapters(config_dir)
        fingerprint, decision = route_input(raw_path, node1_config, adapters=adapters)
    except Exception as exc:  # noqa: BLE001 - structured failure, partial state returned
        _record_exception(state, PipelineStage.ROUTING, exc)
        return result

    state.fingerprint = fingerprint
    state.routing = routing_summary(decision)
    state.routing_identity = routing_identity(decision)
    state.matched_candidates = list(decision.matched_candidates)
    state.run_id = compute_run_id(
        raw_digest=state.raw_digest,
        support_digest=state.support_digest,
        config_versions=state.config_versions,
        reference_date=reference_date,
        routing_identity=state.routing_identity,
    )

    # --- Human mapping-confirmation gate (§1.6) ------------------------------
    if not decision.matched or decision.adapter is None:
        state.stage = PipelineStage.MAPPING_CONFIRMATION
        if mapping_gate is not None and mapping_report is not None:
            try:
                approved = mapping_gate.confirm(mapping_report, fingerprint)
            except Exception as exc:  # noqa: BLE001
                _record_exception(state, PipelineStage.MAPPING_CONFIRMATION, exc)
                return result
            if approved is not None:
                try:
                    mapping_config = persist_confirmed_mapping(
                        approved,
                        config_dir=config_dir or Path(settings.CONFIG_DIR),
                        confirmed_by=mapping_gate.name,
                    )
                except Exception as exc:  # noqa: BLE001
                    _record_exception(state, PipelineStage.MAPPING_CONFIRMATION, exc)
                    return result
                state.mapping_report = approved
                state.mapping_version = mapping_config.mapping_version
                adapters = build_adapters(config_dir)  # pick up the new mapping
                try:
                    fingerprint, decision = route_input(
                        raw_path, node1_config, adapters=adapters
                    )
                except Exception as exc:  # noqa: BLE001
                    _record_exception(state, PipelineStage.ROUTING, exc)
                    return result
                state.routing = routing_summary(decision)
                state.routing_identity = routing_identity(decision)
                state.matched_candidates = list(decision.matched_candidates)
                state.run_id = compute_run_id(
                    raw_digest=state.raw_digest or "",
                    support_digest=state.support_digest,
                    config_versions=state.config_versions,
                    reference_date=reference_date,
                    routing_identity=state.routing_identity,
                )

        if not decision.matched or decision.adapter is None:
            state.status = PipelineStatus.STOPPED_NEEDS_MAPPING
            state.warnings.append(
                "no deterministic adapter matched and no confirmed mapping was supplied; "
                "awaiting human confirmation before continuing"
            )
            return result

    # --- Node 1 (canonicalization + validation) ------------------------------
    state.stage = PipelineStage.NODE1
    try:
        node1_output: Node1Output = run_node1(
            raw_path,
            reference_date=reference_date,
            now=now,
            config=node1_config,
            adapters=adapters,
        )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE1, exc)
        return result
    state.node1_output = node1_output

    if node1_output.validation_report.status == ValidationStatus.FAILED:
        state.status = PipelineStatus.STOPPED_VALIDATION
        state.warnings.append(
            "Node 1 validation failed for the whole batch; the pipeline stops "
            "for this batch (architecture §1.2)"
        )
        return result

    dataset = node1_output.canonical_dataset
    predictors = list(node1_config.approved_core_keys)
    customers = [record.customer_id for record in dataset]

    # --- Node 2 (survival model; fit + score) --------------------------------
    state.stage = PipelineStage.NODE2
    try:
        if dataset and persist_artifact:
            artifact = fit_model(dataset, node2_config, predictors, now=now)
            node2_output = score_to_output(artifact, dataset)
            state.artifact_dir = str(save_artifact(artifact, Path(settings.MODEL_DIR)))
        else:
            node2_output = run_node2(dataset, node2_config, predictors, now=now)
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE2, exc)
        return result
    state.node2_output = node2_output

    # --- Node 3 (support signals) --------------------------------------------
    # Support inputs are optional, but Node 3 always runs for the canonical
    # universe: with no threads it emits an honest ``no_data`` baseline, which is
    # what lets Node 5 publish a report with valid provenance (Node 5 requires a
    # non-empty ``node3_signal_version``). Skipping Node 3 entirely would leave
    # that provenance empty and block publication.
    state.stage = PipelineStage.NODE3
    has_support = bool(support_data) or bool(external_threads)
    has_sources = sources_config is not None and identity_mapping is not None
    if not has_support and not has_sources:
        state.warnings.append(
            "no support data or external sources supplied; Node 3 emitted a no_data "
            "baseline (Node 4 synthesis is effectively quantitative-only)."
        )
    try:
        if has_sources:
            node3_output = run_node3_from_sources(
                customers,
                node3_config,
                sources_config,
                identity_mapping,
                support_data=support_data,
                settings=settings,
                llm_client=llm_client,
                vocabulary=vocabulary,
                now=now,
            )
        else:
            node3_output = run_node3(
                customers,
                support_data,
                node3_config,
                external_threads=external_threads,
                llm_client=llm_client,
                vocabulary=vocabulary,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE3, exc)
        return result
    state.node3_output = node3_output

    # --- Node 4 (deterministic synthesis / ranked accounts) ------------------
    state.stage = PipelineStage.NODE4
    try:
        node4_output = run_node4(node2_output, state.node3_output, node4_config)
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE4, exc)
        return result
    state.node4_output = node4_output

    # --- Node 5 (client-facing report) ---------------------------------------
    state.stage = PipelineStage.NODE5
    try:
        node5_output = run_node5(
            node4_output,
            node5_config,
            customer_data=customer_data,
            node3_output=state.node3_output,
            action_rules=action_rules,
            llm_client=llm_client,
        )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE5, exc)
        return result
    state.node5_output = node5_output

    state.stage = PipelineStage.DONE
    state.status = PipelineStatus.COMPLETED
    return result


def resume_pipeline(
    result: PipelineResult | str | Path,
    *,
    mapping_gate: MappingGate | None = None,
    mapping_report: MappingReport | None = None,
    **kwargs: Any,
) -> PipelineResult:
    """Resume a stopped run by re-entering at routing.

    Loads a persisted ``PipelineResult`` (or accepts one), then calls
    :func:`run_pipeline` again on the original ``raw_path`` with the same config
    versions. Node 1 (and the nodes after it) re-run; this is deliberate — there
    is no hot mid-pipeline resume. Callers must re-supply any Node 3 inputs
    (``support_data`` / sources) and other run kwargs they originally passed.
    """
    if not isinstance(result, PipelineResult):
        result = PipelineResult.load(result)
    state = result.state
    versions = state.config_versions
    for node, key in (
        ("node1_version", "node1"),
        ("node2_version", "node2"),
        ("node3_version", "node3"),
        ("node4_version", "node4"),
        ("node5_version", "node5"),
    ):
        kwargs.setdefault(node, versions.get(key, "1"))
    return run_pipeline(
        state.raw_path,
        mapping_gate=mapping_gate,
        mapping_report=mapping_report or state.mapping_report,
        reference_date=state.reference_date,
        **kwargs,
    )

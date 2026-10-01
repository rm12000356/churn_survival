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

from collections.abc import Callable, Collection, Mapping, Sequence
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
from logging_setup import bind_run_context, clear_run_context, get_logger
from orchestration.identity import (
    CODE_SEMANTICS_VERSION,
    compute_run_id,
    compute_support_digest,
    llm_identity,
    sha256_file,
)
from orchestration.mapping import MappingGate, persist_confirmed_mapping
from orchestration.routing import (
    AUTO_NODE1_VERSION,
    build_adapters,
    resolve_node1_version,
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


def _recommended_node1_version(decision: Any) -> str | None:
    """Deployment Node 1 config recommended by a routing decision, if any."""
    adapter = decision.adapter if decision.matched else None
    recommended = getattr(adapter, "recommended_node1_config", None)
    if callable(recommended):
        version = recommended()
        return str(version) if version else None
    return None


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


def _finish(res: PipelineResult, log: Any) -> PipelineResult:
    """Emit the terminal run event, then clear run-scoped log context (D-H3)."""
    state = res.state
    fields: dict[str, Any] = {
        "status": state.status.value if state.status is not None else None,
        "stage": state.stage.value,
        "run_id": state.run_id,
        "n_warnings": len(state.warnings),
        "n_errors": len(state.errors),
    }
    if state.status == PipelineStatus.COMPLETED:
        log.info("run_completed", **fields)
    elif state.status in (
        PipelineStatus.STOPPED_NEEDS_MAPPING,
        PipelineStatus.STOPPED_VALIDATION,
    ):
        log.info("run_stopped", **fields)
    else:
        log.error("run_failed", **fields)
    clear_run_context()
    return res


def _log_stage(
    log: Any, node: str, *, config_version: str | None = None, **fields: Any
) -> None:
    """Log one node's completion with its config version + output versions/counts."""
    log.info("stage_finished", node=node, config_version=config_version, **fields)


LLM_NODES = frozenset({"node3", "node5"})


def _select_llm_nodes(
    llm_client: Any | None,
    llm_nodes: Collection[str] | None,
    node5_config: Node5Config,
) -> tuple[Any | None, Any | None, Node5Config, str | None]:
    """Route the optional LLM client to the nodes a run selected.

    ``llm_nodes=None`` keeps the legacy behaviour (the client, when given, reaches
    Node 3 and Node 5; Node 5 still honours its config ``llm_enabled``) and adds
    nothing to the run identity. An explicit selection gives the client only to
    those nodes, turns Node 5 explanation polish on/off to match, and is recorded
    as ``config_versions["llm_nodes"]`` so each choice is a distinct run.
    Returns ``(node3_client, node5_client, node5_config, llm_nodes_identity)``.
    """
    if llm_nodes is None:
        return llm_client, llm_client, node5_config, None
    selected = frozenset(llm_nodes)
    unknown = selected - LLM_NODES
    if unknown:
        raise ValueError(f"unknown llm_nodes {sorted(unknown)}; allowed: {sorted(LLM_NODES)}")
    if llm_client is None or not selected:
        return None, None, node5_config.model_copy(update={"llm_enabled": False}), None
    node5_on = "node5" in selected
    return (
        llm_client if "node3" in selected else None,
        llm_client if node5_on else None,
        node5_config.model_copy(update={"llm_enabled": node5_on}),
        ",".join(sorted(selected)),
    )


def _apply_concurrency_overrides(
    node3_config: Node3Config, node5_config: Node5Config, settings: Settings
) -> tuple[Node3Config, Node5Config]:
    """Apply ``NODE{3,5}_LLM_MAX_CONCURRENCY`` deployment overrides (REVIEW §5).

    Concurrency only changes wall-clock time — Node 3 and Node 5 collect results
    in input order — so it is deliberately *not* part of ``config_versions`` /
    ``run_id``: the same inputs keep the same identity at any worker count.
    """
    if settings.NODE3_LLM_MAX_CONCURRENCY is not None:
        node3_config = node3_config.model_copy(
            update={"llm_max_concurrency": settings.NODE3_LLM_MAX_CONCURRENCY}
        )
    if settings.NODE5_LLM_MAX_CONCURRENCY is not None:
        node5_config = node5_config.model_copy(
            update={"llm_max_concurrency": settings.NODE5_LLM_MAX_CONCURRENCY}
        )
    return node3_config, node5_config


def run_pipeline(
    raw_path: str | Path,
    *,
    node1_config: Node1Config | None = None,
    node2_config: Node2Config | None = None,
    node3_config: Node3Config | None = None,
    node4_config: Node4Config | None = None,
    node5_config: Node5Config | None = None,
    node1_version: str = AUTO_NODE1_VERSION,
    node2_version: str = "1",
    node3_version: str = "1",
    node4_version: str = "2",
    node5_version: str = "1",
    support_data: Sequence[SupportThread | dict[str, object]] | None = None,
    external_threads: Sequence[SupportThread | dict[str, object]] | None = None,
    sources_config: Node3SourcesConfig | None = None,
    identity_mapping: IdentityMappingConfig | None = None,
    customer_data: Mapping[str, Any] | None = None,
    action_rules: ActionRulesConfig | None = None,
    llm_client: Any | None = None,
    llm_nodes: Collection[str] | None = None,
    vocabulary: Any | None = None,
    settings: Settings | None = None,
    mapping_gate: MappingGate | None = None,
    mapping_report: MappingReport | None = None,
    mapping_node1_config_version: str | None = None,
    config_dir: str | Path | None = None,
    persist_artifact: bool = False,
    reference_date: date | None = None,
    now: datetime | None = None,
    on_stage: Callable[[PipelineStage], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline on ``raw_path``; always returns a ``PipelineResult``.

    ``config_dir`` scopes confirmed-mapping adapter loading and persistence (it
    defaults to the settings ``CONFIG_DIR``). ``persist_artifact`` is opt-in so a
    demo instance does not accumulate model artifacts by default.

    ``node1_version`` defaults to ``"auto"``: the deployment Node 1 config is
    resolved from the matched confirmed mapping (``MappingConfig.node1_config_version``)
    so an onboarded dataset gets its own ``approved_core_keys``. An explicit version
    always wins. ``mapping_node1_config_version`` records the deployment config on a
    mapping confirmed inline through the gate.
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

    # Node 1 config selection: an explicit config/version wins; ``"auto"`` resolves
    # the deployment config from the matched confirmed mapping before the run
    # identity is hashed (so ``config_versions["node1"]`` is always concrete).
    node1_requested_auto = node1_config is None and node1_version == AUTO_NODE1_VERSION
    node1_warning: str | None = None
    if node1_config is None:
        if node1_version == AUTO_NODE1_VERSION:
            node1_version, node1_warning = resolve_node1_version(
                raw_path, node1_version, config_dir=config_dir
            )
        node1_config = load_node1_config(node1_version)
    elif node1_version == AUTO_NODE1_VERSION:
        # A concrete config object was supplied; keep the identity version concrete.
        node1_version = "1"
    node2_config = node2_config or load_node2_config(node2_version)
    node3_config = node3_config or load_node3_config(node3_version)
    node4_config = node4_config or load_node4_config(node4_version)
    node5_config = node5_config or load_node5_config(node5_version)
    node3_config, node5_config = _apply_concurrency_overrides(
        node3_config, node5_config, settings
    )
    node3_llm, node5_llm, node5_config, llm_nodes_identity = _select_llm_nodes(
        llm_client, llm_nodes, node5_config
    )

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
    if node1_warning:
        state.warnings.append(node1_warning)

    log = get_logger(node="orchestration")
    bind_run_context(run_id=None, reference_date=reference_date.isoformat())
    log.info(
        "run_started",
        raw_file=Path(raw_path).name,
        reference_date=reference_date.isoformat(),
        config_versions=dict(state.config_versions),
    )

    def _stage(value: PipelineStage) -> None:
        """Set the current pipeline stage and report it to ``on_stage``.

        Progress reporting is auxiliary: a failing callback is logged, never
        allowed to abort the run or change its outcome.
        """
        state.stage = value
        if on_stage is not None:
            try:
                on_stage(value)
            except Exception:  # noqa: BLE001 - observability is never fatal
                log.warning("stage_callback_failed", stage=value.value)

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
    llm = llm_identity(node3_llm or node5_llm)
    if llm is not None:
        # LLM-assisted outputs differ from template-only ones: separate identities.
        state.config_versions["llm"] = llm
    if llm_nodes_identity is not None:
        state.config_versions["llm_nodes"] = llm_nodes_identity
    # Output-changing code edits get a new identity too (REVIEW N-H3).
    state.config_versions["semantics"] = CODE_SEMANTICS_VERSION
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
    _stage(PipelineStage.ROUTING)
    try:
        state.raw_digest = sha256_file(raw_path)
        adapters = build_adapters(config_dir)
        fingerprint, decision = route_input(raw_path, node1_config, adapters=adapters)
    except Exception as exc:  # noqa: BLE001 - structured failure, partial state returned
        _record_exception(state, PipelineStage.ROUTING, exc)
        return _finish(result, log)

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
    bind_run_context(run_id=state.run_id, reference_date=reference_date.isoformat())

    # --- Human mapping-confirmation gate (§1.6) ------------------------------
    if not decision.matched or decision.adapter is None:
        _stage(PipelineStage.MAPPING_CONFIRMATION)
        if mapping_gate is not None and mapping_report is not None:
            try:
                approved = mapping_gate.confirm(mapping_report, fingerprint)
            except Exception as exc:  # noqa: BLE001
                _record_exception(state, PipelineStage.MAPPING_CONFIRMATION, exc)
                return _finish(result, log)
            if approved is not None:
                try:
                    mapping_config = persist_confirmed_mapping(
                        approved,
                        config_dir=config_dir or Path(settings.CONFIG_DIR),
                        confirmed_by=mapping_gate.name,
                        node1_config_version=mapping_node1_config_version,
                    )
                except Exception as exc:  # noqa: BLE001
                    _record_exception(state, PipelineStage.MAPPING_CONFIRMATION, exc)
                    return _finish(result, log)
                state.mapping_report = approved
                state.mapping_version = mapping_config.mapping_version
                adapters = build_adapters(config_dir)  # pick up the new mapping
                try:
                    fingerprint, decision = route_input(
                        raw_path, node1_config, adapters=adapters
                    )
                except Exception as exc:  # noqa: BLE001
                    _record_exception(state, PipelineStage.ROUTING, exc)
                    return _finish(result, log)
                # The newly confirmed mapping may recommend a deployment Node 1
                # config; honour it when the caller asked for auto-resolution.
                if node1_requested_auto:
                    recommended = _recommended_node1_version(decision)
                    if recommended and recommended != node1_version:
                        node1_version = recommended
                        node1_config = load_node1_config(node1_version)
                        state.config_versions["node1"] = node1_version
                        state.warnings.append(
                            f"node1 auto-resolve: using deployment config "
                            f"v{node1_version} from the confirmed mapping"
                        )
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
                bind_run_context(
                    run_id=state.run_id, reference_date=reference_date.isoformat()
                )

        if not decision.matched or decision.adapter is None:
            state.status = PipelineStatus.STOPPED_NEEDS_MAPPING
            state.warnings.append(
                "no deterministic adapter matched and no confirmed mapping was supplied; "
                "awaiting human confirmation before continuing"
            )
            return _finish(result, log)

    # --- Node 1 (canonicalization + validation) ------------------------------
    _stage(PipelineStage.NODE1)
    try:
        node1_output: Node1Output = run_node1(
            raw_path,
            reference_date=reference_date,
            now=now,
            config=node1_config,
            adapters=adapters,
            decision=decision,  # routed once above; Node 1 reuses this adapter
        )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE1, exc)
        return _finish(result, log)
    state.node1_output = node1_output
    report = node1_output.validation_report
    _log_stage(
        log,
        "node1",
        config_version=state.config_versions.get("node1"),
        adapter=report.adapter_used,
        validation_status=report.status.value,
        n_accepted=report.n_accepted,
        n_rejected=report.n_rejected,
    )

    if node1_output.validation_report.status == ValidationStatus.FAILED:
        state.status = PipelineStatus.STOPPED_VALIDATION
        state.warnings.append(
            "Node 1 validation failed for the whole batch; the pipeline stops "
            "for this batch (architecture §1.2)"
        )
        return _finish(result, log)

    dataset = node1_output.canonical_dataset
    predictors = list(node1_config.approved_core_keys)
    customers = [record.customer_id for record in dataset]

    # --- Node 2 (survival model; fit + score) --------------------------------
    _stage(PipelineStage.NODE2)
    try:
        if dataset and persist_artifact:
            artifact = fit_model(dataset, node2_config, predictors, now=now)
            node2_output = score_to_output(artifact, dataset)
            state.artifact_dir = str(save_artifact(artifact, Path(settings.MODEL_DIR)))
        else:
            node2_output = run_node2(dataset, node2_config, predictors, now=now)
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE2, exc)
        return _finish(result, log)
    state.node2_output = node2_output
    _log_stage(
        log,
        "node2",
        config_version=state.config_versions.get("node2"),
        model_type=node2_output.model_type.value,
        model_version=node2_output.model_version,
        n_customers=len(node2_output.customer_ids),
    )

    # --- Node 3 (support signals) --------------------------------------------
    # Support inputs are optional, but Node 3 always runs for the canonical
    # universe: with no threads it emits an honest ``no_data`` baseline, which is
    # what lets Node 5 publish a report with valid provenance (Node 5 requires a
    # non-empty ``node3_signal_version``). Skipping Node 3 entirely would leave
    # that provenance empty and block publication.
    _stage(PipelineStage.NODE3)
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
                llm_client=node3_llm,
                vocabulary=vocabulary,
                now=now,
            )
        else:
            node3_output = run_node3(
                customers,
                support_data,
                node3_config,
                external_threads=external_threads,
                llm_client=node3_llm,
                vocabulary=vocabulary,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE3, exc)
        return _finish(result, log)
    state.node3_output = node3_output
    n3 = node3_output.processing_report
    # An LLM outage in Node 3 is a run-level fact (REVIEW N-H5): surface it on
    # the run, not only inside node3.json.
    state.warnings.extend(
        f"node3: {w}" for w in n3.warnings if "LLM extraction" in w or "LLM circuit" in w
    )
    _log_stage(
        log,
        "node3",
        config_version=state.config_versions.get("node3"),
        n_customers_with_signals=n3.n_customers_with_signals,
        n_threads_processed=n3.n_threads_processed,
        n_threads_failed=n3.n_threads_failed,
        llm_calls=n3.llm_calls,
    )

    # --- Node 4 (deterministic synthesis / ranked accounts) ------------------
    _stage(PipelineStage.NODE4)
    try:
        # Support is optional: tell Node 4 whether any was supplied, so a skipped
        # input is not scored as "no support data" for every customer (§4.14a).
        node4_output = run_node4(
            node2_output,
            state.node3_output,
            node4_config,
            support_supplied=has_support or has_sources,
        )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE4, exc)
        return _finish(result, log)
    state.node4_output = node4_output
    accounts = node4_output.ranked_accounts or node4_output.insufficient_data_accounts
    _log_stage(
        log,
        "node4",
        config_version=state.config_versions.get("node4"),
        ranking_version=accounts[0].meta.ranking_version if accounts else None,
        n_ranked=len(node4_output.ranked_accounts),
        n_insufficient=len(node4_output.insufficient_data_accounts),
    )

    # --- Node 5 (client-facing report) ---------------------------------------
    _stage(PipelineStage.NODE5)
    try:
        node5_output = run_node5(
            node4_output,
            node5_config,
            customer_data=customer_data,
            node3_output=state.node3_output,
            action_rules=action_rules,
            llm_client=node5_llm,
        )
    except Exception as exc:  # noqa: BLE001
        _record_exception(state, PipelineStage.NODE5, exc)
        return _finish(result, log)
    state.node5_output = node5_output
    _log_stage(
        log,
        "node5",
        config_version=state.config_versions.get("node5"),
        report_version=node5_output.metadata.report_version,
        n_accounts_reported=node5_output.processing_report.n_accounts_reported,
    )

    _stage(PipelineStage.DONE)
    state.status = PipelineStatus.COMPLETED
    return _finish(result, log)


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

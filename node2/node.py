from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config.loader import load_node2_config
from config.models import Node2Config
from config.settings import get_settings
from node2.artifact import FittedArtifact, build_metadata, derive_model_version, save_artifact
from node2.assumptions import run_assumptions
from node2.cold_start import classify_customers
from node2.cox import (
    feature_associations as cox_feature_associations,
)
from node2.cox import (
    feature_contributions,
    fit_cox,
    risk_reference_time,
    score_risk_scores,
    survival_at_times,
    survival_ci,
)
from node2.cox import (
    forward_survival as cox_forward_survival,
)
from node2.eligibility import EligibilityResult, check_eligibility
from node2.horizons import horizon_statuses as compute_horizon_statuses
from node2.interpretation import interpret_hazard_ratio
from node2.kaplan_meier import fit_km, loglog_ci, window_terms
from node2.kaplan_meier import forward_survival as km_forward_survival
from node2.kaplan_meier import survival_at_times as km_survival_at_times
from node2.matrix import FeatureSpec, build_specs, encode, modeling_values
from node2.status import decide_status
from schemas.canonical import CanonicalRecord
from schemas.enums import CustomerState, HorizonStatus, ModelStatus, ModelType
from schemas.node2 import (
    FeatureAssociation,
    FeatureContribution,
    ForwardHorizonResult,
    HorizonResult,
    Node2Output,
)


def derive_dataset_version(
    matrix: pd.DataFrame,
    reference_date: date,
    predictors: Sequence[str],
    config: Node2Config,
) -> str:
    h = hashlib.sha256()
    h.update(str(reference_date).encode("utf-8"))
    h.update("\x00".join(sorted(predictors)).encode("utf-8"))
    h.update(json.dumps(config.model_dump(mode="json"), sort_keys=True).encode("utf-8"))
    for column in matrix.columns:
        h.update(column.encode("utf-8"))
        h.update(json.dumps(matrix[column].tolist(), allow_nan=True).encode("utf-8"))
    return h.hexdigest()[:12]


def _reference_date(records: Sequence[CanonicalRecord]) -> date:
    if not records:
        raise ValueError("no canonical records to fit on")
    reference_dates = {record.meta.reference_date for record in records}
    if len(reference_dates) != 1:
        raise ValueError(
            "all canonical records must share one reference_date; "
            f"got {sorted(str(d) for d in reference_dates)}"
        )
    return next(iter(reference_dates))


def _seed(dataset_version: str) -> int:
    return int(hashlib.sha256(dataset_version.encode("utf-8")).hexdigest()[:8], 16)


def _prepare(
    records: Sequence[CanonicalRecord],
    predictors: Sequence[str],
    config: Node2Config,
) -> tuple[list[CanonicalRecord], dict[str, CustomerState], list[FeatureSpec], Any]:
    ordered = sorted(records, key=lambda record: record.customer_id)
    states = classify_customers(ordered, predictors, config)
    scored = [record for record in ordered if states[record.customer_id] == CustomerState.SCORED]
    specs = build_specs(scored, predictors)
    matrix = encode(_rows_for(scored), specs)
    return ordered, states, specs, matrix


def _finite_or_none(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _rows_for(scored: Sequence[CanonicalRecord]) -> list[tuple[str, dict[str, Any], float, int]]:
    return [
        (
            record.customer_id,
            modeling_values(record),
            float(record.tenure),
            record.event_observed,
        )
        for record in scored
    ]


def _feature_kind(feature: str, specs: Sequence[FeatureSpec]) -> str:
    for spec in specs:
        if feature == spec.name or feature.startswith(f"{spec.name}_"):
            return spec.kind
    return "categorical"


def fit_model(
    canonical_dataset: Sequence[CanonicalRecord],
    config: Node2Config,
    predictors: Sequence[str],
    *,
    dataset_version: str | None = None,
    now: datetime | None = None,
) -> FittedArtifact:
    records = list(canonical_dataset)
    if not records:
        raise ValueError("cannot fit a model on an empty canonical dataset")

    reference_date = _reference_date(records)
    ordered, states, specs, matrix = _prepare(records, predictors, config)
    if dataset_version is None:
        dataset_version = derive_dataset_version(matrix, reference_date, predictors, config)
    seed = _seed(dataset_version)
    n_input = len(ordered)
    n_customers = int(len(matrix))
    n_events = int(matrix["event"].sum()) if n_customers else 0

    if n_customers:
        eligibility = check_eligibility(matrix, specs, config, n_input_records=n_input)
        horizon_statuses = compute_horizon_statuses(matrix, config)
        km = fit_km(matrix, specs, config, timeline=[float(t) for t in config.horizons])
        km_fitted = True
    else:
        eligibility = EligibilityResult(
            eligible=False,
            hard_failures=("no scored customers after complete-case + cold-start exclusion",),
        )
        horizon_statuses = {t: HorizonStatus.INSUFFICIENT_DATA for t in config.horizons}
        km = None
        km_fitted = False

    t_ref = (
        risk_reference_time(
            matrix,
            horizon_90_available=(horizon_statuses.get(90) == HorizonStatus.AVAILABLE),
        )
        if n_customers
        else 0.0
    )

    warnings: list[str] = list(eligibility.warnings)
    cph = None
    assumption = None
    if eligibility.eligible and n_customers:
        try:
            cph = fit_cox(matrix, config)
            assumption = run_assumptions(cph, matrix, specs, config, seed=seed, t_ref=t_ref)
            if assumption.decision == "stratify" and assumption.refitted_model is not None:
                cph = assumption.refitted_model
            elif assumption.decision == "fallback":
                cph = None
        except Exception as exc:  # noqa: BLE001 - a technical fit failure must never crash silently
            warnings.append(f"CoxPH fit failed (technical): {exc}")
            cph = None

    if cph is None and n_customers and not eligibility.eligible:
        warnings.append(
            "model eligibility failed; fell back to Kaplan-Meier: "
            + "; ".join(eligibility.hard_failures)
        )
    if cph is None and assumption is not None and assumption.decision == "fallback":
        warnings.append(
            "serious proportional-hazards violation with no viable stratification; "
            "fell back to Kaplan-Meier"
        )

    model_type = (
        ModelType.COX_PH
        if cph is not None
        else (ModelType.KAPLAN_MEIER if km_fitted else ModelType.NONE)
    )
    model_status = decide_status(
        n_input_records=n_input,
        eligible=eligibility.eligible,
        cox_fit_succeeded=(cph is not None),
        assumption_severity=assumption.severity if assumption else "none",
        assumption_decision=assumption.decision if assumption else "fallback",
        eligibility_warnings=bool(eligibility.warnings),
        km_fitted=km_fitted,
    )

    coefficients: dict[str, float] = {}
    associations: tuple[FeatureAssociation, ...] = ()
    if cph is not None:
        coefficients = {str(k): float(v) for k, v in cph.params_.items()}
        associations = tuple(
            FeatureAssociation(
                feature=item["feature"],
                coefficient=item["coefficient"],
                hazard_ratio=item["hazard_ratio"],
                ci_lower=item["ci_lower"],
                ci_upper=item["ci_upper"],
                p_value=item["p_value"],
                interpretation=interpret_hazard_ratio(
                    item["hazard_ratio"],
                    kind=_feature_kind(item["feature"], specs),
                    ci_lower=item["ci_lower"],
                    ci_upper=item["ci_upper"],
                    p_value=item["p_value"],
                ),
            )
            for item in cox_feature_associations(cph)
        )

    encoding_scheme = {
        spec.name: {
            "kind": spec.kind,
            "categories": list(spec.categories),
            "reference_category": (
                spec.categories[0]
                if spec.kind == "categorical" and spec.categories
                else None
            ),
        }
        for spec in specs
    }
    baseline: dict[str, Any] = {
        "risk_reference_time_days": t_ref,
        "tie_method": config.tie_method,
        "strata_used": assumption.strata_used if cph is not None and assumption else None,
    }
    validation_metrics: dict[str, Any] = {}
    assumption_check_results: dict[str, Any] = {}
    if cph is not None and assumption is not None:
        validation_metrics = {
            "c_index": assumption.c_index,
            "c_index_ci_lower": assumption.c_index_ci[0] if assumption.c_index_ci else None,
            "c_index_ci_upper": assumption.c_index_ci[1] if assumption.c_index_ci else None,
            "bootstrap_iterations": config.bootstrap_iterations,
            "c_index_kind": "apparent",
            "c_index_score": (
                "risk_score_t_ref" if assumption.strata_used else "partial_hazard"
            ),
        }
        assumption_check_results = {
            "ph_p_values": assumption.ph_p_values,
            "severity": assumption.severity,
            "decision": assumption.decision,
            "strata_used": assumption.strata_used,
        }
        if assumption.severity_after_refit is not None:
            assumption_check_results["ph_p_values_after_refit"] = (
                assumption.ph_p_values_after_refit
            )
            assumption_check_results["severity_after_refit"] = assumption.severity_after_refit
    else:
        assumption_check_results = {"model_type": model_type.value, "status": model_status.value}

    metadata = build_metadata(
        model_version=derive_model_version(
            reference_date=reference_date,
            dataset_version=dataset_version,
            config=config,
            selected_features=list(predictors),
        ),
        dataset_version=dataset_version,
        reference_date=reference_date,
        selected_features=list(predictors),
        coefficients=coefficients,
        baseline=baseline,
        penalizer=config.penalizer,
        n_customers=n_customers,
        n_events=n_events,
        encoding_scheme=encoding_scheme,
        validation_metrics=validation_metrics,
        assumption_check_results=assumption_check_results,
        horizon_config=list(config.horizons),
        training_timestamp=now,
    )

    return FittedArtifact(
        metadata=metadata,
        model=cph,
        km=km,
        specs=tuple(specs),
        fit_data=matrix,
        t_ref=t_ref,
        horizon_statuses=horizon_statuses,
        model_type=model_type,
        model_status=model_status,
        config=config,
        warnings=tuple(warnings),
        feature_associations=associations,
    )


def _score(
    artifact: FittedArtifact,
    customers: Sequence[CanonicalRecord],
) -> dict[str, Any]:
    predictors = [spec.name for spec in artifact.specs]
    ordered = sorted(customers, key=lambda record: record.customer_id)
    states = classify_customers(ordered, predictors, artifact.config)
    scored = [record for record in ordered if states[record.customer_id] == CustomerState.SCORED]
    matrix = encode(_rows_for(scored), artifact.specs)

    risk_scores: list[float] | None = None
    baseline_log_hazard: float | None = None
    relative_log_hazard: list[float | None] | None = None
    contributions: list[list[FeatureContribution]] | None = None
    if artifact.model is not None and len(matrix):
        risk_scores = score_risk_scores(artifact.model, matrix, artifact.t_ref).tolist()
        baseline_log_hazard, relative, contributions = feature_contributions(
            artifact.model, matrix, artifact.fit_data, artifact.specs
        )
        relative_log_hazard = list(relative)

    survival_probabilities: dict[str, HorizonResult] = {}
    for t in artifact.config.horizons:
        key = f"{t}d"
        if artifact.horizon_statuses.get(t) != HorizonStatus.AVAILABLE or len(matrix) == 0:
            survival_probabilities[key] = HorizonResult(status=HorizonStatus.INSUFFICIENT_DATA)
            continue
        if artifact.model is not None:
            sf = survival_at_times(artifact.model, matrix, [float(t)])
            values = [float(v) for v in sf.loc[t].tolist()]
            ci_map = survival_ci(artifact.model, matrix, artifact.fit_data, [float(t)])
            lo, hi = ci_map[float(t)]
            ci = [[_finite_or_none(lo[i]), _finite_or_none(hi[i])] for i in range(len(lo))]
            approximate = True
        else:
            values, km_ci = km_survival_at_times(artifact.km, matrix, [float(t)])[float(t)]
            ci = [[_finite_or_none(lo), _finite_or_none(hi)] for lo, hi in km_ci]
            approximate = False
        survival_probabilities[key] = HorizonResult(
            status=HorizonStatus.AVAILABLE,
            values=values,
            ci=ci,
            ci_approximate=approximate,
        )

    max_follow_up = (
        float(artifact.fit_data["duration"].max()) if len(artifact.fit_data) else None
    )
    forward = _forward_survival(artifact, matrix, max_follow_up)

    return {
        "model_type": artifact.model_type,
        "model_status": artifact.model_status,
        "model_version": artifact.metadata.model_version,
        "risk_scores": risk_scores,
        "survival_probabilities": survival_probabilities,
        "customer_ids": [record.customer_id for record in ordered],
        "customer_states": [states[record.customer_id].value for record in ordered],
        "customer_tenure_days": [float(record.tenure) for record in ordered],
        "customer_event_observed": [int(record.event_observed) for record in ordered],
        "forward_survival": forward,
        "max_follow_up_days": max_follow_up,
        "baseline_log_hazard": baseline_log_hazard,
        "customer_relative_log_hazard": relative_log_hazard,
        "customer_contributions": contributions,
        "warnings": list(artifact.warnings),
    }


def _forward_survival(
    artifact: FittedArtifact,
    matrix: pd.DataFrame,
    max_follow_up: float | None,
) -> dict[str, ForwardHorizonResult] | None:
    if artifact.km is None or max_follow_up is None or len(matrix) == 0:
        return None
    tenure = matrix["duration"].to_numpy(dtype=float)
    churned = matrix["event"].to_numpy(dtype=int) == 1
    result: dict[str, ForwardHorizonResult] = {}
    for t in artifact.config.horizons:
        if artifact.horizon_statuses.get(t) != HorizonStatus.AVAILABLE:
            continue
        horizon = float(t)
        valid = (~churned) & (tenure + horizon <= max_follow_up)
        points: list[float]
        greenwood: list[float | None]
        if artifact.model is not None:
            cox_values = cox_forward_survival(artifact.model, matrix, horizon)
            global_terms: dict[float, float | None] = {}
            points = []
            greenwood = []
            for i, start in enumerate(tenure):
                if start not in global_terms:
                    global_terms[start] = window_terms(
                        artifact.km.global_curve, float(start), horizon
                    )[1]
                points.append(float(cox_values[i]))
                greenwood.append(global_terms[start])
            approximate = True
        else:
            points, greenwood = km_forward_survival(artifact.km, matrix, horizon)
            approximate = False
        values: list[float | None] = []
        ci: list[list[float | None]] = []
        for i, ok in enumerate(valid):
            value = _finite_or_none(points[i]) if ok else None
            if value is None:
                values.append(None)
                ci.append([None, None])
                continue
            value = min(1.0, max(0.0, value))
            values.append(value)
            ci.append(loglog_ci(value, greenwood[i]))
        result[f"{t}d"] = ForwardHorizonResult(values=values, ci=ci, ci_approximate=approximate)
    return result


def score_customers(
    artifact: FittedArtifact,
    customers: Sequence[CanonicalRecord],
) -> dict[str, Any]:
    return _score(artifact, customers)


def score_to_output(artifact: FittedArtifact, customers: Sequence[CanonicalRecord]) -> Node2Output:
    scored = _score(artifact, customers)
    feature_associations = (
        list(artifact.feature_associations) if artifact.model_type == ModelType.COX_PH else None
    )
    return Node2Output(
        model_type=scored["model_type"],
        model_status=scored["model_status"],
        model_version=scored["model_version"],
        risk_scores=scored["risk_scores"],
        survival_probabilities=scored["survival_probabilities"],
        feature_associations=feature_associations,
        validation_metrics=artifact.metadata.validation_metrics,
        assumption_checks=artifact.metadata.assumption_check_results,
        warnings=scored["warnings"],
        customer_ids=scored["customer_ids"],
        customer_states=[CustomerState(state) for state in scored["customer_states"]],
        customer_tenure_days=scored["customer_tenure_days"],
        customer_event_observed=scored["customer_event_observed"],
        forward_survival=scored["forward_survival"],
        max_follow_up_days=scored["max_follow_up_days"],
        baseline_log_hazard=scored["baseline_log_hazard"],
        customer_relative_log_hazard=scored["customer_relative_log_hazard"],
        customer_contributions=scored["customer_contributions"],
    )


def run_node2(
    canonical_dataset: Sequence[CanonicalRecord],
    config: Node2Config,
    predictors: Sequence[str],
    *,
    dataset_version: str | None = None,
    now: datetime | None = None,
) -> Node2Output:
    if not canonical_dataset:
        return Node2Output(
            model_type=ModelType.NONE,
            model_status=ModelStatus.INSUFFICIENT_DATA,
            model_version="",
            risk_scores=None,
            survival_probabilities={
                f"{t}d": HorizonResult(status=HorizonStatus.INSUFFICIENT_DATA)
                for t in config.horizons
            },
            warnings=["no canonical records to fit or score"],
        )
    artifact = fit_model(
        canonical_dataset, config, predictors, dataset_version=dataset_version, now=now
    )
    return score_to_output(artifact, canonical_dataset)


def _usage() -> str:
    return (
        "churn-survival node2 <raw-file> "
        "[--config <node1_version>] [--model-config <node2_version>]"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    node1_version = "1"
    if "--config" in args:
        flag_index = args.index("--config")
        if flag_index + 1 >= len(args):
            print(f"Usage: {_usage()}", file=sys.stderr)
            return 2
        node1_version = args[flag_index + 1]
        args = args[:flag_index] + args[flag_index + 2 :]
    node2_version = "1"
    if "--model-config" in args:
        flag_index = args.index("--model-config")
        if flag_index + 1 >= len(args):
            print(f"Usage: {_usage()}", file=sys.stderr)
            return 2
        node2_version = args[flag_index + 1]
        args = args[:flag_index] + args[flag_index + 2 :]
    if len(args) != 1:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    from config.loader import load_node1_config
    from logging_setup import emit_node_completion
    from node1.node import run_node1

    try:
        node1_config = load_node1_config(node1_version)
        node2_config = load_node2_config(node2_version)
        node1_output = run_node1(args[0], config=node1_config)
        dataset = node1_output.canonical_dataset
        if not dataset:
            print(
                "Node 2: type=none status=INSUFFICIENT_DATA — "
                "no canonical records to fit or score"
            )
            emit_node_completion("node2", config_version=node2_version, model_type="none")
            return 0
        predictors = node1_config.model_predictors
        artifact = fit_model(dataset, node2_config, predictors)
        output = score_to_output(artifact, dataset)
        artifact_dir = save_artifact(artifact, Path(get_settings().MODEL_DIR))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: Node 2 failed: {exc}", file=sys.stderr)
        return 1

    available = [t for t, h in artifact.horizon_statuses.items() if h == HorizonStatus.AVAILABLE]
    print(
        f"Node 2: type={output.model_type.value} status={output.model_status.value} "
        f"n_customers={artifact.metadata.n_customers} n_events={artifact.metadata.n_events} "
        f"penalizer={artifact.metadata.penalizer}"
    )
    print(
        f"Node 2: horizons available={available or 'none'} "
        f"model_version={artifact.metadata.model_version}"
    )
    print(f"Node 2: artifact persisted -> {artifact_dir}")
    emit_node_completion(
        "node2",
        config_version=node2_version,
        model_type=output.model_type.value,
        model_status=output.model_status.value,
        model_version=artifact.metadata.model_version,
        n_customers=artifact.metadata.n_customers,
        n_events=artifact.metadata.n_events,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

from schemas.enums import ModelStatus


def risk_scores_emitted(status: ModelStatus) -> bool:
    return status in (ModelStatus.READY, ModelStatus.WARNING)


def decide_status(
    *,
    n_input_records: int,
    eligible: bool,
    cox_fit_succeeded: bool,
    assumption_severity: str,
    assumption_decision: str,
    eligibility_warnings: bool,
    km_fitted: bool,
) -> ModelStatus:
    if n_input_records <= 0:
        return ModelStatus.INSUFFICIENT_DATA

    if eligible and cox_fit_succeeded:
        if assumption_decision == "fallback":
            return ModelStatus.FALLBACK if km_fitted else ModelStatus.FAILED
        if (
            eligibility_warnings
            or assumption_severity == "minor"
            or assumption_decision == "stratify"
        ):
            return ModelStatus.WARNING
        return ModelStatus.READY

    if km_fitted:
        return ModelStatus.FALLBACK

    if eligible and not cox_fit_succeeded:
        return ModelStatus.FAILED

    return ModelStatus.INSUFFICIENT_DATA

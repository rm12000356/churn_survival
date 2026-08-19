"""Model status state machine (architecture §2.3, ROADMAP Task 3.11).

``decide_status`` is a pure function mapping the pipeline outcomes to the §2.3
status. ``risk_scores`` may be emitted only for READY/WARNING — individual
Cox-style scores are null for every other status.
"""

from __future__ import annotations

from schemas.enums import ModelStatus


def risk_scores_emitted(status: ModelStatus) -> bool:
    """§2.3: risk_scores are emitted only for READY/WARNING."""
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
    """Deterministic status for the §2.13 flow.

    - no data at all -> INSUFFICIENT_DATA
    - CoxPH fitted and kept -> READY unless limitations (warnings/minor PH
      violation/stratified refit) downgrade to WARNING
    - serious PH violation with no viable stratification -> KM fallback
    - not eligible / technical fit failure -> KM fallback when possible
    - no model at all -> INSUFFICIENT_DATA / FAILED
    """
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

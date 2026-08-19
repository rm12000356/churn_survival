"""Model status state machine (ROADMAP Task 3.11/§2.3)."""

from __future__ import annotations

from node2.status import decide_status, risk_scores_emitted
from schemas.enums import ModelStatus


def test_risk_scores_only_for_ready_warning() -> None:
    assert risk_scores_emitted(ModelStatus.READY)
    assert risk_scores_emitted(ModelStatus.WARNING)
    assert not risk_scores_emitted(ModelStatus.FALLBACK)
    assert not risk_scores_emitted(ModelStatus.INSUFFICIENT_DATA)
    assert not risk_scores_emitted(ModelStatus.FAILED)


def test_no_data_is_insufficient() -> None:
    assert (
        decide_status(
            n_input_records=0,
            eligible=False,
            cox_fit_succeeded=False,
            assumption_severity="none",
            assumption_decision="fallback",
            eligibility_warnings=False,
            km_fitted=False,
        )
        == ModelStatus.INSUFFICIENT_DATA
    )


def test_clean_cox_is_ready() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=True,
            assumption_severity="none",
            assumption_decision="keep",
            eligibility_warnings=False,
            km_fitted=True,
        )
        == ModelStatus.READY
    )


def test_eligibility_warning_downgrades_to_warning() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=True,
            assumption_severity="none",
            assumption_decision="keep",
            eligibility_warnings=True,
            km_fitted=True,
        )
        == ModelStatus.WARNING
    )


def test_minor_ph_violation_downgrades_to_warning() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=True,
            assumption_severity="minor",
            assumption_decision="keep",
            eligibility_warnings=False,
            km_fitted=True,
        )
        == ModelStatus.WARNING
    )


def test_stratified_refit_is_warning() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=True,
            assumption_severity="serious",
            assumption_decision="stratify",
            eligibility_warnings=False,
            km_fitted=True,
        )
        == ModelStatus.WARNING
    )


def test_ph_fallback_with_km_is_fallback() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=False,
            assumption_severity="serious",
            assumption_decision="fallback",
            eligibility_warnings=False,
            km_fitted=True,
        )
        == ModelStatus.FALLBACK
    )


def test_fitted_but_ph_fallback_with_km_is_fallback() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=True,
            assumption_severity="serious",
            assumption_decision="fallback",
            eligibility_warnings=False,
            km_fitted=True,
        )
        == ModelStatus.FALLBACK
    )


def test_ph_fallback_without_km_is_failed() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=True,
            cox_fit_succeeded=False,
            assumption_severity="serious",
            assumption_decision="fallback",
            eligibility_warnings=False,
            km_fitted=False,
        )
        == ModelStatus.FAILED
    )


def test_not_eligible_with_km_is_fallback() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=False,
            cox_fit_succeeded=False,
            assumption_severity="none",
            assumption_decision="fallback",
            eligibility_warnings=True,
            km_fitted=True,
        )
        == ModelStatus.FALLBACK
    )


def test_not_eligible_without_km_is_insufficient() -> None:
    assert (
        decide_status(
            n_input_records=10,
            eligible=False,
            cox_fit_succeeded=False,
            assumption_severity="none",
            assumption_decision="fallback",
            eligibility_warnings=True,
            km_fitted=False,
        )
        == ModelStatus.INSUFFICIENT_DATA
    )

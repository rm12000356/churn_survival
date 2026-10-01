"""Deterministic per-account driver wording (architecture §2.12b/§4.4b amendment).

Presentation only: renders Node 4 ``driver_details`` (this account's positive
contributions to relative log-hazard) against the *model reference profile* —
numerics at their training mean, categoricals at their reference category.
Hazard language only; never "portfolio average" or "relative risk"; non-causal.
"""

from __future__ import annotations

from schemas.node2 import DriverDetail

MODEL_REFERENCE_PROFILE = "the model reference profile"


def format_number(value: float) -> str:
    """Human rendering of a driver value (persisted values stay full precision)."""
    return f"{value:.4g}" if abs(value) < 1000 else f"{value:.1f}"


def number_forms(detail: DriverDetail) -> list[float | str]:
    """Numbers this module may print for a driver (registered as allowed facts)."""
    forms: list[float | str] = [detail.hazard_ratio, detail.contribution]
    for value in (detail.value, detail.reference):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            forms.extend([float(value), format_number(float(value))])
    return forms


def _numeric_pair(detail: DriverDetail) -> tuple[float, float] | None:
    if isinstance(detail.value, (int, float)) and isinstance(detail.reference, (int, float)):
        return float(detail.value), float(detail.reference)
    return None


def _side(value: float, reference: float) -> str:
    return "below" if value < reference else "above"


def short_phrase(detail: DriverDetail) -> str:
    """``usage_frequency (below the reference profile)`` / ``plan_tier = starter``."""
    pair = _numeric_pair(detail)
    if detail.kind == "categorical" or pair is None:
        return f"{detail.feature} = {detail.value}"
    return f"{detail.feature} ({_side(*pair)} the reference profile)"


def evidence_description(details: list[DriverDetail]) -> str:
    phrases = "; ".join(short_phrase(detail) for detail in details)
    return f"Model drivers relative to {MODEL_REFERENCE_PROFILE}: {phrases}."


def detail_phrase(detail: DriverDetail) -> str:
    """Directional phrase with values, used by the HTML report."""
    if detail.kind == "categorical":
        return f"{detail.feature} = {detail.value} (reference category: {detail.reference})"
    pair = _numeric_pair(detail)
    if pair is None:
        return f"{detail.feature} = {detail.value}"
    value, reference = pair
    return (
        f"{detail.feature} = {format_number(value)} ({_side(value, reference)} "
        f"{MODEL_REFERENCE_PROFILE}, {format_number(reference)})"
    )


def summary_sentence(detail: DriverDetail, relative_log_hazard: float | None) -> str:
    """One template-summary sentence naming the account's strongest driver.

    The overall "above the reference profile" claim is made only when the
    account's total relative log-hazard is positive; a driver can raise the
    hazard while other features pull the total below the reference profile.
    """
    if relative_log_hazard is not None and relative_log_hazard > 0.0:
        lead = (
            f"The model rates this account's churn hazard above {MODEL_REFERENCE_PROFILE} "
            "mainly because"
        )
    else:
        lead = "The model feature raising this account's churn hazard most is that"
    pair = _numeric_pair(detail)
    if detail.kind == "categorical" or pair is None:
        reference = (
            f" (reference category: {detail.reference})" if detail.reference is not None else ""
        )
        return f"{lead} the account is on {detail.feature} = {detail.value}{reference}."
    value, ref = pair
    return (
        f"{lead} {detail.feature} ({format_number(value)}) is {_side(value, ref)} the "
        f"reference profile value ({format_number(ref)})."
    )

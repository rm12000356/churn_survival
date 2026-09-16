"""Milestone B/C — deterministic explanation validation (architecture §5.28, D-VAL)."""

from __future__ import annotations

from node5.report.explanation_validator import (
    build_allowed_facts,
    validate_explanation,
)
from schemas.enums import FlagType
from tests.node5.conftest import make_sample_node4


def _allowed(customer_id: str, recommendation: str | None = None):
    account = next(
        a for a in make_sample_node4().ranked_accounts if a.customer_id == customer_id
    )
    return build_allowed_facts(account, recommendation)


def test_supported_text_is_accepted() -> None:
    allowed = _allowed("A")
    violations = validate_explanation(
        "Critical risk: cancellation intent detected",
        "The account shows explicit cancellation intent in recent support interactions.",
        ["Cancellation intent was detected."],
        allowed,
    )
    assert violations == []


def test_implied_different_risk_level_is_rejected() -> None:
    allowed = _allowed("B")  # high
    violations = validate_explanation(
        "Critical risk",
        "This account is extremely critical.",
        [],
        allowed,
    )
    assert any(v.startswith("RISK_LEVEL_MISMATCH") for v in violations)


def test_invented_cancellation_claim_is_rejected() -> None:
    allowed = _allowed("B")  # billing complaint only
    violations = validate_explanation(
        "High risk",
        "The customer has indicated that they want to cancel.",
        [],
        allowed,
    )
    assert any(v.startswith("UNSUPPORTED_CANCELLATION_CLAIM") for v in violations)


def test_unsupported_number_is_rejected() -> None:
    allowed = _allowed("A")
    violations = validate_explanation("Critical risk", "The score is 0.123.", [], allowed)
    assert any(v.startswith("UNSUPPORTED_NUMBER") for v in violations)


def test_unsupported_date_is_rejected() -> None:
    allowed = _allowed("A")
    violations = validate_explanation("Critical risk", "Seen on 1999-01-01.", [], allowed)
    assert any(v.startswith("UNSUPPORTED_DATE") for v in violations)


def test_unsupported_evidence_id_is_rejected() -> None:
    allowed = _allowed("A")
    violations = validate_explanation(
        "Critical risk", "Evidence msg_secret_42 supports this.", [], allowed
    )
    assert any(v.startswith("UNSUPPORTED_EVIDENCE") for v in violations)


def test_confidence_as_probability_is_rejected() -> None:
    allowed = _allowed("A")
    violations = validate_explanation(
        "Critical risk", "There is a 50% chance of churn.", [], allowed
    )
    assert any(v.startswith("CONFIDENCE_AS_PROBABILITY") for v in violations)


def test_unsupported_recommendation_is_rejected() -> None:
    allowed = _allowed("A", recommendation=None)
    violations = validate_explanation(
        "Critical risk", "We recommend contacting the account immediately.", [], allowed
    )
    assert any(v.startswith("UNSUPPORTED_RECOMMENDATION") for v in violations)


def test_allowed_facts_track_cancellation_flag() -> None:
    assert _allowed("A").cancellation_present is True
    assert _allowed("B").cancellation_present is False
    assert FlagType.CANCELLATION_INTENT.value in _allowed("A").flag_types


# --- F-2: recommendations ------------------------------------------------- #
_UNSUPPORTED_RECOMMENDATIONS = (
    "Offer the customer a discount.",
    "Offer a 30% discount.",
    "Give them a refund.",
    "Upgrade the account.",
    "Escalate this customer.",
    "Contact sales immediately.",
    "Provide a retention incentive.",
)


def test_actionable_recommendations_are_rejected_without_a_selection() -> None:
    allowed = _allowed("D", recommendation=None)  # no deterministic recommendation
    for text in _UNSUPPORTED_RECOMMENDATIONS:
        violations = validate_explanation("Low risk", text, [], allowed)
        assert any(v.startswith("UNSUPPORTED_RECOMMENDATION") for v in violations), text


def test_selected_recommendation_language_is_allowed() -> None:
    allowed = _allowed("A", recommendation="Contact the account to discuss cancellation concerns.")
    violations = validate_explanation(
        "Critical risk",
        "The account should be contacted to discuss cancellation concerns.",
        [],
        allowed,
    )
    assert not any(v.startswith("UNSUPPORTED_RECOMMENDATION") for v in violations)


# --- F-2: risk factors ---------------------------------------------------- #
def test_unvalidated_risk_factors_are_rejected() -> None:
    allowed = _allowed("B")  # billing complaint only
    for text in (
        "The customer is considering a competitor.",
        "Customer usage has declined significantly.",
        "There is a renewal concern for this account.",
        "The account experienced a product outage.",
        "The customer reported poor support.",
    ):
        violations = validate_explanation("High risk", text, [], allowed)
        assert any(v.startswith("UNSUPPORTED_RISK_FACTOR") for v in violations), text


def test_present_risk_factor_is_allowed() -> None:
    allowed = _allowed("B")
    violations = validate_explanation(
        "High risk", "There is a billing complaint on record.", [], allowed
    )
    assert not any(v.startswith("UNSUPPORTED_RISK_FACTOR") for v in violations)


# --- F-2: material customer facts ----------------------------------------- #
def test_material_customer_facts_are_rejected() -> None:
    allowed = _allowed("A")
    for text in (
        "The customer has been with us for eight years.",
        "The customer contacted support three times.",
        "The customer recently changed their plan.",
        "The company acquired the account.",
    ):
        violations = validate_explanation("Critical risk", text, [], allowed)
        assert any(v.startswith("UNSUPPORTED_CUSTOMER_FACT") for v in violations), text


# --- F-2: numeric claims -------------------------------------------------- #
def test_percentages_are_never_allowed() -> None:
    allowed = _allowed("A")  # combined_score may be ~0.99
    violations = validate_explanation("Critical risk", "A 30% discount.", [], allowed)
    assert any(v.startswith("UNSUPPORTED_NUMBER") for v in violations)


def test_spelled_out_numbers_are_checked() -> None:
    allowed = _allowed("A")
    for word in ("three", "eight", "twenty"):
        violations = validate_explanation("Critical risk", f"{word} times.", [], allowed)
        assert any(v.startswith("UNSUPPORTED_NUMBER") for v in violations), word


# --- F-2/F-6: dates ------------------------------------------------------- #
def test_may_as_a_verb_is_not_a_date() -> None:
    allowed = _allowed("A")
    violations = validate_explanation(
        "Critical risk", "The customer may leave us soon.", [], allowed
    )
    assert not any(v.startswith("UNSUPPORTED_DATE") for v in violations)


def test_real_date_expressions_are_validated() -> None:
    allowed = _allowed("A")
    for text in ("May 2026", "May 15", "15 May 2026", "May 15, 2026"):
        violations = validate_explanation("Critical risk", text, [], allowed)
        assert any(v.startswith("UNSUPPORTED_DATE") for v in violations), text


def test_reference_month_is_allowed() -> None:
    allowed = _allowed("A")
    violations = validate_explanation(
        "Critical risk", "The reference date is August 2026.", [], allowed
    )
    assert not any(v.startswith("UNSUPPORTED_DATE") for v in violations)


# --- F-2: risk-level claims ------------------------------------------------ #
def test_risk_level_phrasing_is_rejected() -> None:
    allowed = _allowed("B")  # high
    for text in ("critical churn risk", "Critical — cancel", "medium risk"):
        violations = validate_explanation(text, "", [], allowed)
        assert any(v.startswith("RISK_LEVEL_MISMATCH") for v in violations), text

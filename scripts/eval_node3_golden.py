from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklearn.metrics import cohen_kappa_score

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.loader import load_node3_config, load_vocabulary  # noqa: E402
from node3.node import run_node3  # noqa: E402
from schemas.enums import FlagType  # noqa: E402
from schemas.node3 import SupportThread  # noqa: E402

THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"

_COHORT_TO_FLAG: dict[str, FlagType] = {
    "strong_cancellation_intent": FlagType.CANCELLATION_INTENT,
    "moderate_cancellation_intent": FlagType.CANCELLATION_INTENT,
    "weak_cancellation_intent": FlagType.CANCELLATION_INTENT,
    "cancellation_intent_conflict": FlagType.CANCELLATION_INTENT,
    "renewal_or_contract_concern": FlagType.RENEWAL_OR_CONTRACT_CONCERN,
    "repeated_issue": FlagType.PRODUCT_BUG_OR_OUTAGE,
    "high_urgency": FlagType.POOR_SUPPORT_EXPERIENCE,
    "low_information": FlagType.OTHER,
    "positive_feedback": FlagType.POSITIVE_FEEDBACK,
}
_STRENGTH_LABELS = ("none", "weak", "moderate", "strong")

KAPPA_FLAG_TARGET = 0.70
KAPPA_STRENGTH_TARGET = 0.65
EXACT_MATCH_TARGET = 0.75

REPRESENTATIVE_NOW = datetime(2026, 8, 15, tzinfo=UTC)


@dataclass(frozen=True)
class GoldenResult:
    n_customers: int
    kappa_flag_type: float
    kappa_signal_strength: float
    cancellation_exact_match: float
    renewal_exact_match: float
    passed: bool
    failures: list[str] = field(default_factory=list)


def _expected_flags(record: dict) -> set[FlagType]:
    return {_COHORT_TO_FLAG[f] for f in record["expected_flags"] if f in _COHORT_TO_FLAG}


def _indicator_accuracy(oracle: list[bool], predicted: list[bool]) -> float:
    if not oracle:
        return 0.0
    return sum(a == b for a, b in zip(oracle, predicted, strict=True)) / len(oracle)


def evaluate_golden(
    config: str = "dataset7", *, live: bool = False, client: Any | None = None
) -> GoldenResult:
    cfg = load_node3_config(config)
    vocabulary = load_vocabulary()
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    support_truth: dict = truth["support_truth"]
    raw_threads = json.loads(THREADS_JSON.read_text(encoding="utf-8"))
    threads = [SupportThread.model_validate(entry) for entry in raw_threads]
    customers = list(support_truth.keys())

    resolved_client = client
    if live and resolved_client is None:
        from router.llm_mapper import create_llm_client

        resolved_client = create_llm_client()

    output = run_node3(
        customers,
        threads,
        cfg,
        llm_client=resolved_client,
        vocabulary=vocabulary,
        now=REPRESENTATIVE_NOW,
    )
    predicted_by_customer = {s.customer_id: s for s in output.customer_signals}

    oracle_types = sorted(
        {
            _COHORT_TO_FLAG[f]
            for record in support_truth.values()
            for f in record["expected_flags"]
            if f in _COHORT_TO_FLAG
        },
        key=lambda f: f.value,
    )
    oracle_indicators: list[bool] = []
    pred_indicators: list[bool] = []
    for cid, record in support_truth.items():
        expected = _expected_flags(record)
        predicted = {flag.flag_type for flag in predicted_by_customer[cid].risk_flags}
        for flag_type in oracle_types:
            oracle_indicators.append(flag_type in expected)
            pred_indicators.append(flag_type in predicted)

    kappa_flags = cohen_kappa_score(oracle_indicators, pred_indicators)

    strength_oracle: list[str] = []
    strength_pred: list[str] = []
    for cid, record in support_truth.items():
        strength_oracle.append(str(record["expected_signal_strength"]))
        strength_pred.append(predicted_by_customer[cid].signal_strength.value)
    kappa_strength = cohen_kappa_score(
        strength_oracle, strength_pred, labels=list(_STRENGTH_LABELS)
    )

    cancel_oracle = [
        FlagType.CANCELLATION_INTENT in _expected_flags(rec) for rec in support_truth.values()
    ]
    cancel_pred = [
        any(
            f.flag_type is FlagType.CANCELLATION_INTENT
            for f in predicted_by_customer[cid].risk_flags
        )
        for cid in support_truth
    ]
    renewal_oracle = [
        FlagType.RENEWAL_OR_CONTRACT_CONCERN in _expected_flags(rec)
        for rec in support_truth.values()
    ]
    renewal_pred = [
        any(
            f.flag_type is FlagType.RENEWAL_OR_CONTRACT_CONCERN
            for f in predicted_by_customer[cid].risk_flags
        )
        for cid in support_truth
    ]
    cancel_match = _indicator_accuracy(cancel_oracle, cancel_pred)
    renewal_match = _indicator_accuracy(renewal_oracle, renewal_pred)

    failures: list[str] = []
    if kappa_flags < KAPPA_FLAG_TARGET:
        failures.append(f"kappa(flag_type) {kappa_flags:.3f} < {KAPPA_FLAG_TARGET}")
    if kappa_strength < KAPPA_STRENGTH_TARGET:
        failures.append(f"kappa(signal_strength) {kappa_strength:.3f} < {KAPPA_STRENGTH_TARGET}")
    if cancel_match < EXACT_MATCH_TARGET:
        failures.append(f"cancellation exact-match {cancel_match:.3f} < {EXACT_MATCH_TARGET}")
    if renewal_match < EXACT_MATCH_TARGET:
        failures.append(f"renewal exact-match {renewal_match:.3f} < {EXACT_MATCH_TARGET}")

    return GoldenResult(
        n_customers=len(customers),
        kappa_flag_type=kappa_flags,
        kappa_signal_strength=kappa_strength,
        cancellation_exact_match=cancel_match,
        renewal_exact_match=renewal_match,
        passed=not failures,
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Node 3 golden-set evaluation")
    parser.add_argument("--live", action="store_true", help="use the configured LLM provider")
    parser.add_argument("--config", default="dataset7", help="Node 3 config version")
    args = parser.parse_args(argv)

    result = evaluate_golden(args.config, live=args.live)

    print("Node 3 golden-set evaluation (dataset7 proxy)")
    print(f"  customers                  : {result.n_customers}")
    print(
        f"  kappa(flag_type)           : {result.kappa_flag_type:.3f}  "
        f"(target >= {KAPPA_FLAG_TARGET})"
    )
    print(
        f"  kappa(signal_strength)     : {result.kappa_signal_strength:.3f}  "
        f"(target >= {KAPPA_STRENGTH_TARGET})"
    )
    print(
        f"  exact-match cancellation   : {result.cancellation_exact_match:.3f}  "
        f"(target >= {EXACT_MATCH_TARGET})"
    )
    print(
        f"  exact-match renewal        : {result.renewal_exact_match:.3f}  "
        f"(target >= {EXACT_MATCH_TARGET})"
    )

    if result.failures:
        print("FAILED acceptance bars:")
        for failure in result.failures:
            print(f"  - {failure}")
        return 1
    print("All acceptance bars met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Node 3 golden-set evaluation harness (architecture §3.10, ROADMAP Task 4.13).

Dataset 7 is the master diagnostic corpus. Its ``support_truth`` records the
*expected* flag population as generator cohort labels, not the architecture's
controlled vocabulary, so this harness maps cohorts -> ``FlagType`` (below) and
measures agreement on the normalized oracle. It computes Cohen's kappa on
``flag_type`` and ``signal_strength`` plus exact-match on the two highest-risk
flags, against the §3.10 acceptance bars.

This is a *proxy* golden set: the architecture's bar calls for 150-300
double-annotated real threads; see docs/dataset7_addendum_v1.2.md.

Limitation: the oracle only annotates the flag types that map from generator
cohorts (cancellation_intent, renewal_or_contract_concern, product_bug_or_outage,
poor_support_experience, positive_feedback, other). κ is therefore computed only
over those types; predicted flags outside the oracle's vocabulary (e.g.
billing_complaint) are not scored, and the implementation is not tuned to this
harness.

Usage:
    python scripts/eval_node3_golden.py            # deterministic offline extractor
    python scripts/eval_node3_golden.py --live     # configured LLM provider
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

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

# cohort label (generator) -> controlled vocabulary flag_type
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


def _expected_flags(record: dict) -> set[FlagType]:
    return {_COHORT_TO_FLAG[f] for f in record["expected_flags"] if f in _COHORT_TO_FLAG}


def _indicator_accuracy(oracle: list[bool], predicted: list[bool]) -> float:
    if not oracle:
        return 0.0
    return sum(a == b for a, b in zip(oracle, predicted, strict=True)) / len(oracle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Node 3 golden-set evaluation")
    parser.add_argument("--live", action="store_true", help="use the configured LLM provider")
    parser.add_argument("--config", default="dataset7", help="Node 3 config version")
    args = parser.parse_args(argv)

    config = load_node3_config(args.config)
    vocabulary = load_vocabulary()
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    support_truth: dict = truth["support_truth"]
    raw_threads = json.loads(THREADS_JSON.read_text(encoding="utf-8"))
    threads = [SupportThread.model_validate(entry) for entry in raw_threads]
    customers = list(support_truth.keys())

    client = None
    if args.live:
        from router.llm_mapper import create_llm_client

        client = create_llm_client()

    output = run_node3(
        customers,
        threads,
        config,
        llm_client=client,
        vocabulary=vocabulary,
        now=datetime(2026, 8, 15, tzinfo=UTC),
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

    print("Node 3 golden-set evaluation (dataset7 proxy)")
    print(f"  customers                  : {len(customers)}")
    print(f"  evaluated flag types       : {', '.join(f.value for f in oracle_types)}")
    print(f"  kappa(flag_type)           : {kappa_flags:.3f}  (target >= {KAPPA_FLAG_TARGET})")
    print(
        f"  kappa(signal_strength)     : {kappa_strength:.3f}  "
        f"(target >= {KAPPA_STRENGTH_TARGET})"
    )
    print(f"  exact-match cancellation   : {cancel_match:.3f}  (target >= {EXACT_MATCH_TARGET})")
    print(f"  exact-match renewal        : {renewal_match:.3f}  (target >= {EXACT_MATCH_TARGET})")

    failures = []
    if kappa_flags < KAPPA_FLAG_TARGET:
        failures.append(f"kappa(flag_type) {kappa_flags:.3f} < {KAPPA_FLAG_TARGET}")
    if kappa_strength < KAPPA_STRENGTH_TARGET:
        failures.append(f"kappa(signal_strength) {kappa_strength:.3f} < {KAPPA_STRENGTH_TARGET}")
    if cancel_match < EXACT_MATCH_TARGET:
        failures.append(f"cancellation exact-match {cancel_match:.3f} < {EXACT_MATCH_TARGET}")
    if renewal_match < EXACT_MATCH_TARGET:
        failures.append(f"renewal exact-match {renewal_match:.3f} < {EXACT_MATCH_TARGET}")

    if failures:
        print("FAILED acceptance bars:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All acceptance bars met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

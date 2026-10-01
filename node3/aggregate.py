"""Node 3 deterministic aggregation (architecture §3.6–§3.8, ROADMAP Tasks 4.8–4.10).

Recency weighting, flag recurrence, the locked evidence-quality / overall-
confidence formulas, and customer-level field derivations. Collapsed duplicate
threads and quarantined (failed) threads are excluded from all signal counts.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Collection, Sequence
from datetime import datetime

from config.models import Node3Config, VocabularyConfig
from node3.clock import run_timestamp
from node3.vocabulary import STRENGTH_SCORE, get_hierarchy_rank, get_vocabulary
from schemas.enums import (
    FlagType,
    LanguageStatus,
    OverallSignalStrength,
    SentimentLabel,
    Severity,
    SupportDataStatus,
    UrgencyLevel,
)
from schemas.node3 import (
    AggregatedRiskFlag,
    CustomerSupportSignals,
    CustomerSupportSignalsMeta,
    RiskFlag,
    Sentiment,
    ThreadSignals,
)

_CLARITY_KEYWORDS = ("cancel", "cancelling", "terminate", "switch", "leave", "refund")
_URGENCY_ORDER = {
    UrgencyLevel.UNKNOWN: 0,
    UrgencyLevel.LOW: 1,
    UrgencyLevel.MEDIUM: 2,
    UrgencyLevel.HIGH: 3,
}


def lambda_for(flag_type: FlagType, config: Node3Config) -> float:
    """Persistent-lambda for cancellation/renewal flags, default otherwise (§3.8.1)."""
    return (
        config.lambda_persistent
        if flag_type.value in config.persistent_flag_types
        else config.lambda_default
    )


def adjusted_strength(flag: RiskFlag, age_days: int, config: Node3Config) -> float:
    """``STRENGTH_SCORE[strength] * exp(-lambda * age_days)`` (§3.8.1)."""
    return STRENGTH_SCORE[flag.signal_strength] * math.exp(
        -lambda_for(flag.flag_type, config) * age_days
    )


# --------------------------------------------------------------------------- #
# Locked formulas (§3.8.4 / §3.8.5)
# --------------------------------------------------------------------------- #
def compute_evidence_quality_score(flags: Sequence[RiskFlag]) -> float:
    """Locked §3.8.4 evidence-quality score."""
    if not flags:
        return 0.0
    scores: list[float] = []
    for flag in flags:
        text = flag.evidence.text
        length_score = min(1.0, len(text.split()) / 12)
        clarity_bonus = 0.25 if any(w in text.lower() for w in _CLARITY_KEYWORDS) else 0.0
        scores.append(min(1.0, 0.75 * length_score + clarity_bonus))
    return round(sum(scores) / len(scores), 3)


def compute_overall_signal_confidence(
    support_data_status: SupportDataStatus | str,
    n_customer_messages: int,
    n_usable_threads: int,
    schema_validity_rate: float,
    supported_language_coverage: float,
    evidence_quality_score: float,
) -> float:
    """Locked §3.8.5 overall-signal-confidence formula."""
    status = (
        support_data_status.value
        if isinstance(support_data_status, SupportDataStatus)
        else support_data_status
    )
    if status == SupportDataStatus.NO_DATA.value:
        return 0.0

    volume_score = min(1.0, (n_customer_messages / 8) ** 0.5)
    thread_score = min(1.0, n_usable_threads / 3)

    confidence = (
        0.20 * volume_score
        + 0.20 * thread_score
        + 0.20 * schema_validity_rate
        + 0.15 * supported_language_coverage
        + 0.25 * evidence_quality_score
    )
    confidence = float(confidence)
    return round(min(1.0, max(0.0, confidence)), 3)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _aggregate_flags(
    usable: Sequence[ThreadSignals], config: Node3Config, vocabulary: VocabularyConfig
) -> list[AggregatedRiskFlag]:
    by_type: dict[FlagType, list[tuple[ThreadSignals, RiskFlag]]] = {}
    for signals in usable:
        for flag in signals.risk_flags:
            by_type.setdefault(flag.flag_type, []).append((signals, flag))

    aggregated: list[tuple[int, float, AggregatedRiskFlag]] = []
    for flag_type, instances in by_type.items():
        age_days = [
            (config.reference_date - signals.created_at.date()).days for signals, _ in instances
        ]
        strongest_index = max(
            range(len(instances)),
            key=lambda i: (
                adjusted_strength(instances[i][1], age_days[i], config),
                instances[i][0].created_at,
                instances[i][1].evidence.message_id,
            ),
        )
        strongest = instances[strongest_index][1]
        first = min(flag.evidence.timestamp for _, flag in instances)
        last = max(flag.evidence.timestamp for _, flag in instances)
        message_ids = list(
            OrderedDict.fromkeys(
                mid
                for _, flag in instances
                for mid in [*flag.evidence_message_ids, flag.evidence.message_id]
            )
        )
        rank = get_hierarchy_rank(flag_type, vocabulary)
        best_strength = adjusted_strength(strongest, age_days[strongest_index], config)
        aggregated.append(
            (
                rank,
                -best_strength,
                AggregatedRiskFlag(
                    flag_type=flag_type,
                    severity=strongest.severity,
                    signal_strength=strongest.signal_strength,
                    confidence=strongest.confidence,
                    recurrence_count=len(instances),
                    first_observed_at=first,
                    last_observed_at=last,
                    strongest_evidence=strongest.evidence,
                    evidence_message_ids=message_ids,
                ),
            )
        )

    aggregated.sort(key=lambda item: (item[0], item[1], item[2].flag_type.value))
    return [flag for _, _, flag in aggregated]


def _overall_sentiment(
    usable: Sequence[ThreadSignals], config: Node3Config
) -> Sentiment:
    """Recency-weighted average sentiment (§3.8.3).

    The "(same λ)" wording is read as the default recency λ: sentiment is not a
    risk flag and has no persistent flag type, so ``lambda_default`` applies to
    every thread.
    """
    weighted, weights, confidences = 0.0, 0.0, []
    for signals in usable:
        score = signals.sentiment.score
        if score is None:
            continue
        age_days = (config.reference_date - signals.created_at.date()).days
        weight = math.exp(-config.lambda_default * age_days)
        weighted += weight * score
        weights += weight
        confidences.append(signals.sentiment.confidence)
    if weights == 0.0:
        return Sentiment(label=SentimentLabel.UNKNOWN, score=None, confidence=0.0)
    score = round(weighted / weights, 3)
    if score > 0.25:
        label = SentimentLabel.POSITIVE
    elif score < -0.25:
        label = SentimentLabel.NEGATIVE
    else:
        label = SentimentLabel.NEUTRAL
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    return Sentiment(label=label, score=score, confidence=confidence)


def _summary(
    status: SupportDataStatus,
    flags: Sequence[AggregatedRiskFlag],
    urgency: UrgencyLevel,
    n_threads: int,
) -> str | None:
    if status is SupportDataStatus.NO_DATA or not flags:
        return None
    primary = flags[0]
    parts = [
        f"{primary.recurrence_count} support thread(s) flagged "
        f"{primary.flag_type.value} ({primary.severity.value} severity).",
        f"Urgency {urgency.value}; {n_threads} thread(s) in window.",
    ]
    return " ".join(parts)


def aggregate_customer(
    customer_id: str,
    thread_signals: Sequence[ThreadSignals],
    config: Node3Config,
    *,
    failed_thread_ids: Collection[str] = (),
    vocabulary: VocabularyConfig | None = None,
    now: datetime | None = None,
) -> CustomerSupportSignals:
    """Aggregate one customer's thread signals into ``CustomerSupportSignals``."""
    now = run_timestamp(config, now)
    vocab = vocabulary or get_vocabulary()
    failed = set(failed_thread_ids)

    in_window = [s for s in thread_signals if s.duplicate_of is None]
    usable = [s for s in in_window if s.thread_id not in failed]

    n_threads = len(in_window)
    n_messages = sum(s.meta.n_customer_messages + s.meta.n_agent_messages for s in in_window)
    # §3.8.6 "enough customer-authored content for meaningful extraction": a
    # quarantined thread (unsupported language, failed extraction) contributed
    # nothing extractable, so it must not make the status/volume look sufficient.
    n_usable_customer_messages = sum(s.meta.n_customer_messages for s in usable)
    latest = None
    for signals in in_window:
        candidate = signals.latest_message_at or signals.created_at
        if latest is None or candidate > latest:
            latest = candidate

    if n_threads == 0:
        status = SupportDataStatus.NO_DATA
    elif n_usable_customer_messages < config.limited_data_min_customer_messages or not usable:
        status = SupportDataStatus.LIMITED_DATA
    else:
        status = SupportDataStatus.SUFFICIENT_DATA

    aggregated_flags = _aggregate_flags(usable, config, vocab)
    all_flags = [flag for signals in usable for flag in signals.risk_flags]
    evidence_quality = compute_evidence_quality_score(all_flags)
    schema_validity_rate = round(len(usable) / n_threads, 3) if n_threads else 0.0
    supported = sum(
        1 for s in in_window if s.language_status is LanguageStatus.SUPPORTED
    )
    language_coverage = round(supported / n_threads, 3) if n_threads else 0.0
    confidence = compute_overall_signal_confidence(
        status,
        n_usable_customer_messages,
        len(usable),
        schema_validity_rate,
        language_coverage,
        evidence_quality,
    )

    if aggregated_flags:
        strength = OverallSignalStrength(aggregated_flags[0].signal_strength.value)
    else:
        strength = OverallSignalStrength.NONE

    urgency = UrgencyLevel.UNKNOWN
    if usable:
        urgency = max(
            (s.urgency_level for s in usable), key=lambda u: _URGENCY_ORDER[u]
        )

    escalation = any(s.urgency_level is UrgencyLevel.HIGH for s in usable) or any(
        flag.severity is Severity.HIGH and flag.recurrence_count >= 2
        for flag in aggregated_flags
    )
    churn_language = any(s.churn_language_detected for s in usable)

    themes: list[str] = []
    for signals in usable:
        for theme in signals.key_themes:
            if theme not in themes:
                themes.append(theme)

    return CustomerSupportSignals(
        customer_id=customer_id,
        support_data_status=status,
        has_support_data=n_threads > 0,
        n_threads_in_window=n_threads,
        n_messages_in_window=n_messages,
        latest_interaction_at=latest,
        overall_sentiment=_overall_sentiment(usable, config),
        risk_flags=aggregated_flags,
        signal_strength=strength,
        overall_signal_confidence=confidence,
        key_themes=themes,
        urgency_level=urgency,
        escalation_signal=escalation,
        churn_language_detected=churn_language,
        summary=_summary(status, aggregated_flags, urgency, n_threads),
        meta=CustomerSupportSignalsMeta(
            lookback_days=config.lookback_days,
            processed_at=now,
            prompt_version=config.prompt_version,
            model_version=(
                in_window[0].meta.model_version if in_window else "offline"
            ),
            aggregation_version=config.aggregation_version,
            vocabulary_version=config.vocabulary_version,
            preprocessing_version=config.preprocessing_version,
            reference_date=config.reference_date,
        ),
    )

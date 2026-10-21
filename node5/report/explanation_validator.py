from __future__ import annotations

import re
from dataclasses import dataclass, field

from node5.report.driver_text import number_forms
from schemas.enums import FlagType
from schemas.node4 import RankedAccount

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?%?")
_EVIDENCE_ID = re.compile(r"\b(?:msg|thr)_[A-Za-z0-9_]+")
_LEVEL_WORDS = {"critical", "high", "medium", "low"}

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)
_MONTH_ALT = "|".join(_MONTHS)
_DATE_EXPR = re.compile(
    rf"\b(?:{_MONTH_ALT})\s+\d{{1,2}}(?:,\s*\d{{4}})?\b"
    rf"|\b(?:{_MONTH_ALT})\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTH_ALT})(?:\s+\d{{4}})?\b",
    re.IGNORECASE,
)

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}
_WORD_NUMBER_RE = re.compile(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", re.IGNORECASE)

_CANCELLATION_PHRASES = ("cancel", "terminat", "end the contract", "leave us")
_PROBABILITY_PHRASES = (
    "% chance", "chance of", "probability of churn", "likely to churn",
    "probability that",
)
_GREEN_PHRASES = (
    "will remain active", "remain active", "stays active", "stay active", "no risk",
)

_FLAG_CONCEPTS: dict[FlagType, tuple[str, ...]] = {
    FlagType.COMPETITOR_MENTION: (
        "competitor", "competition", "competing", "switch to", "switching to",
        "switching provider", "alternative provider", "another provider",
        "another vendor", "other vendor",
    ),
    FlagType.USAGE_DROP_RELATED: (
        "usage decline", "usage drop", "declining usage", "declining activity",
        "reduced usage", "reduced activity", "usage has declined", "less active",
    ),
    FlagType.RENEWAL_OR_CONTRACT_CONCERN: (
        "renewal concern", "contract concern", "renewal", "contract renewal",
        "contract expiry", "contract expiring",
    ),
    FlagType.PRODUCT_BUG_OR_OUTAGE: (
        "outage", "product bug", "product issue", "product problem", "downtime",
        "system failure",
    ),
    FlagType.POOR_SUPPORT_EXPERIENCE: (
        "poor support", "unresolved complaint", "support experience", "bad support",
        "support issues",
    ),
    FlagType.BILLING_COMPLAINT: (
        "billing complaint", "billing issue", "invoice", "overcharged",
        "billing problem", "billing", "pricing", "price", "expensive", "overpriced",
        "too costly", "cost concern",
    ),
    FlagType.FEATURE_MISSING: (
        "missing feature", "feature request", "requested functionality", "feature gap",
    ),
}

_DISSATISFACTION_PHRASES = (
    "frustrat", "dissatisf", "unhappy", "disappointed", "annoyed", "upset",
)

_FACT_PHRASES = (
    "has been with us", "has been a customer", "has been our customer",
    "years of experience", "long-time customer", "long time customer",
    "contacted support", "contacted us", "reached out to support",
    "changed their plan", "changed plans", "changed their subscription",
    "signed up", "onboarded",
    "acquired", "acquisition", "merger", "merged", "bankrupt", "laid off",
    "headquarters", "subsidiary",
)

_ACTION_VOCAB = (
    "offer", "discount", "refund", "upgrade", "downgrade", "escalate",
    "compensate", "compensation", "waive", "retention", "incentive",
    "contact sales", "reach out", "schedule", "provide", "credit", "reimburse",
    "apologize", "expedite", "upsell", "cross-sell", "migrate",
    "contact", "call", "follow up", "follow-up", "meeting", "reach the",
)


@dataclass
class AllowedFacts:
    risk_level: str
    customer_id: str = ""
    display_name: str = ""
    decimals: set[str] = field(default_factory=set)
    integers: set[int] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    reference_month: str = ""
    reference_year: int = 0
    flag_types: set[str] = field(default_factory=set)
    message_ids: set[str] = field(default_factory=set)
    thread_ids: set[str] = field(default_factory=set)
    recommendation: str | None = None
    action_words: set[str] = field(default_factory=set)
    cancellation_present: bool = False


def _norm(token: str) -> str:
    try:
        return f"{float(token):.6g}"
    except ValueError:
        return token


def _decimal_forms(value: float | None) -> set[str]:
    if value is None:
        return set()
    forms = {_norm(str(float(value)))}
    for fmt in ("{:.1f}", "{:.2f}", "{:.3f}", "{:.4f}"):
        forms.add(_norm(fmt.format(float(value))))
    return forms


def _action_words(recommendation: str | None) -> set[str]:
    words = {"recommend", "recommended"}
    if recommendation:
        words |= {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z\-']+", recommendation)}
    return words


def build_allowed_facts(
    account: RankedAccount,
    recommendation: str | None = None,
    display_name: str | None = None,
) -> AllowedFacts:
    decimals: set[str] = set()
    integers: set[int] = {90}
    for value in (
        account.combined_score,
        account.combined_confidence,
        account.quantitative.risk_score,
        account.quantitative.survival_prob_90d,
        account.quantitative.churn_prob_90d_forward,
        account.quantitative.lift_vs_base,
    ):
        decimals |= _decimal_forms(value)

    for detail in account.quantitative.driver_details:
        for form in number_forms(detail):
            decimals |= _decimal_forms(float(form))
    decimals |= _decimal_forms(account.quantitative.relative_log_hazard)

    flag_types: set[str] = set()
    for entry in account.qualitative.top_flags:
        if isinstance(entry, dict) and entry.get("flag_type"):
            flag_types.add(str(entry["flag_type"]))
            recurrence = entry.get("recurrence_count")
            if isinstance(recurrence, (int, float)) and not isinstance(recurrence, bool):
                integers.add(int(recurrence))
                decimals.add(_norm(str(int(recurrence))))

    message_ids = set(account.evidence_refs.node3.message_ids)
    for reason in account.primary_reasons:
        ref = reason.evidence_ref
        if not isinstance(ref, dict):
            continue
        if ref.get("flag_type"):
            flag_types.add(str(ref["flag_type"]))
        for message_id in ref.get("message_ids", []) or []:
            if isinstance(message_id, str):
                message_ids.add(message_id)

    reference = account.meta.ranked_at.date()
    return AllowedFacts(
        risk_level=account.combined_risk_level.value,
        customer_id=account.customer_id,
        display_name=display_name or account.customer_id,
        decimals=decimals,
        integers=integers,
        dates={reference.isoformat()},
        reference_month=reference.strftime("%B").lower(),
        reference_year=reference.year,
        flag_types=flag_types,
        message_ids=message_ids,
        thread_ids=set(account.evidence_refs.node3.thread_ids),
        recommendation=recommendation,
        action_words=_action_words(recommendation),
        cancellation_present=FlagType.CANCELLATION_INTENT.value in flag_types,
    )


def _violation(code: str, detail: str) -> str:
    return f"{code}: {detail}"


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)


def _check_dates(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    for token in _ISO_DATE.findall(text):
        if token not in allowed.dates:
            violations.append(_violation("UNSUPPORTED_DATE", token))
    for token in _TIME.findall(text):
        violations.append(_violation("UNSUPPORTED_TIMESTAMP", token))
    for match in _DATE_EXPR.finditer(text):
        expression = match.group(0)
        lowered = expression.lower()
        years = re.findall(r"\d{4}", expression)
        wrong_year = any(year != str(allowed.reference_year) for year in years)
        if allowed.reference_month not in lowered or wrong_year:
            violations.append(_violation("UNSUPPORTED_DATE", expression))


def _check_numbers(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    date_spans = [match.span() for match in _DATE_EXPR.finditer(text)]
    stripped = _blank_spans(_TIME.sub(" ", _ISO_DATE.sub(" ", text)), date_spans)
    for token in _NUMBER.findall(stripped):
        if token.endswith("%"):
            violations.append(_violation("UNSUPPORTED_NUMBER", token))
            continue
        if _norm(token) in allowed.decimals:
            continue
        if token.isdigit() and int(token) in allowed.integers:
            continue
        violations.append(_violation("UNSUPPORTED_NUMBER", token))
    for match in _WORD_NUMBER_RE.finditer(_ISO_DATE.sub(" ", text)):
        word = match.group(1).lower()
        if _WORD_NUMBERS[word] not in allowed.integers:
            violations.append(_violation("UNSUPPORTED_NUMBER", word))


def _check_evidence_ids(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    for token in _EVIDENCE_ID.findall(text):
        if token not in allowed.message_ids and token not in allowed.thread_ids:
            violations.append(_violation("UNSUPPORTED_EVIDENCE", token))


def _check_level(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    lowered = text.lower()
    for word in _LEVEL_WORDS:
        if word == allowed.risk_level:
            continue
        if re.search(rf"\b{word}\b", lowered):
            violations.append(_violation("RISK_LEVEL_MISMATCH", word))


def _check_risk_factors(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    lowered = text.lower()
    for flag_type, phrases in _FLAG_CONCEPTS.items():
        if flag_type.value in allowed.flag_types:
            continue
        for phrase in phrases:
            if phrase in lowered:
                violations.append(_violation("UNSUPPORTED_RISK_FACTOR", phrase))
    risk_flags = allowed.flag_types - {FlagType.POSITIVE_FEEDBACK.value}
    if not risk_flags:
        for phrase in _DISSATISFACTION_PHRASES:
            if phrase in lowered:
                violations.append(_violation("UNSUPPORTED_RISK_FACTOR", phrase))


def _mask_identity(text: str, allowed: AllowedFacts) -> str:
    for value in sorted({allowed.customer_id, allowed.display_name}, key=len, reverse=True):
        if value.strip():
            pattern = rf"(?<!\w){re.escape(value.strip())}(?!\w)"
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return text


def _check_cancellation(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    if allowed.cancellation_present:
        return
    lowered = text.lower()
    for phrase in _CANCELLATION_PHRASES:
        if phrase in lowered:
            violations.append(_violation("UNSUPPORTED_CANCELLATION_CLAIM", phrase))


def _check_customer_facts(text: str, violations: list[str]) -> None:
    lowered = text.lower()
    for phrase in _FACT_PHRASES:
        if phrase in lowered:
            violations.append(_violation("UNSUPPORTED_CUSTOMER_FACT", phrase))


def _check_probability(text: str, violations: list[str]) -> None:
    lowered = text.lower()
    for phrase in _PROBABILITY_PHRASES:
        if phrase in lowered:
            violations.append(_violation("CONFIDENCE_AS_PROBABILITY", phrase))


def _check_recommendation(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    lowered = text.lower()
    for phrase in _ACTION_VOCAB:
        if phrase in lowered and phrase not in allowed.action_words:
            violations.append(_violation("UNSUPPORTED_RECOMMENDATION", phrase))


def _check_contradiction(text: str, allowed: AllowedFacts, violations: list[str]) -> None:
    if allowed.risk_level not in {"critical", "high"}:
        return
    lowered = text.lower()
    for phrase in _GREEN_PHRASES:
        if phrase in lowered:
            violations.append(_violation("CONTRADICTS_NODE4", phrase))


def validate_explanation(
    headline: str,
    summary: str,
    reason_explanations: list[str],
    allowed: AllowedFacts,
) -> list[str]:
    text = _mask_identity("\n".join([headline, summary, *reason_explanations]), allowed)
    violations: list[str] = []
    _check_numbers(text, allowed, violations)
    _check_dates(text, allowed, violations)
    _check_evidence_ids(text, allowed, violations)
    _check_customer_facts(text, violations)
    _check_risk_factors(text, allowed, violations)
    _check_level(text, allowed, violations)
    _check_cancellation(text, allowed, violations)
    _check_probability(text, violations)
    _check_recommendation(text, allowed, violations)
    _check_contradiction(text, allowed, violations)
    return violations

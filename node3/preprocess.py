"""Node 3 deterministic preprocessing (architecture §3.3, ROADMAP Tasks 4.2–4.4).

Cleaning, lookback windowing, message deduplication, cross-channel near-duplicate
collapse, deterministic language detection, and hard-limit truncation. Every step
is deterministic and versioned via ``Node3Config.preprocessing_version``; all
original ``message_id``s and timestamps are preserved.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import ValidationError

from config.models import Node3Config
from schemas.enums import LanguageStatus
from schemas.node3 import SupportMessage, SupportThread

_WHITESPACE = re.compile(r"\s+")


@dataclass
class PreprocessedThread:
    """A cleaned thread plus its deterministic language/duplicate annotations.

    ``language_status`` and ``duplicate_of`` live on ``ThreadSignals`` (not on the
    input ``SupportThread``), so preprocessing carries them alongside the cleaned
    thread.
    """

    thread: SupportThread
    language: str | None
    language_status: LanguageStatus
    duplicate_of: str | None = None


@dataclass
class PreprocessingStats:
    """Counters and structured errors surfaced in the Node 3 processing report."""

    n_input_threads: int = 0
    n_dropped_invalid: int = 0
    n_out_of_window: int = 0
    n_system_messages_removed: int = 0
    n_duplicate_messages_removed: int = 0
    n_duplicates_collapsed: int = 0
    n_threads_over_limit: int = 0
    n_unsupported_language: int = 0
    n_unknown_language: int = 0
    errors: list[dict[str, object]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def normalize_text(text: str) -> str:
    """Lower-case + collapse whitespace for deterministic comparisons."""
    return _WHITESPACE.sub(" ", text.strip().lower())


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: whitespace-delimited word count."""
    return len(text.split())


def _customer_message_texts(thread: SupportThread) -> list[str]:
    return [m.text for m in thread.messages if m.role == "customer"]


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


# --------------------------------------------------------------------------- #
# Language detection (§3.3 / Task 4.4)
# --------------------------------------------------------------------------- #
# Deterministic stopword/script scoring for the supported + dataset7 languages.
_LANGUAGE_ORDER = ("en", "es", "de", "fr")
_LANGUAGE_STOPWORDS: dict[str, set[str]] = {
    "en": {
        "the", "and", "to", "of", "a", "is", "it", "you", "we", "i", "this",
        "that", "my", "can", "please", "help", "how", "do", "not", "for", "on",
    },
    "es": {
        "el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "por",
        "para", "con", "no", "mi", "es", "como", "pero", "muy", "estoy", "ayuda",
    },
    "de": {
        "der", "die", "das", "und", "ich", "nicht", "ist", "zu", "den", "mit",
        "ein", "eine", "für", "auf", "sie", "wir", "bitte", "kann", "wie", "mein",
    },
    "fr": {
        "le", "la", "les", "de", "et", "un", "une", "je", "ne", "pas", "que",
        "pour", "avec", "est", "dans", "sur", "mon", "vous", "nous", "aide",
    },
}
_SCRIPT_MARKERS: dict[str, tuple[str, ...]] = {
    "es": ("ñ", "¿", "¡", "á", "é", "í", "ó", "ú"),
    "de": ("ß", "ä", "ö", "ü"),
    "fr": ("ç", "è", "ê", "à", "ù", "œ", "â"),
}


def detect_language(texts: Sequence[str]) -> str | None:
    """Deterministic language code (en/es/de/fr) or ``None`` when unknown.

    Scores each candidate language by stopword overlap plus script markers; a
    language wins only with a strictly positive score. Ties break by the fixed
    key order (en, es, de, fr) for determinism.
    """
    joined = " ".join(texts).lower()
    words = set(_WHITESPACE.sub(" ", joined).split())
    if not words:
        return None

    scores = {
        code: len(words & _LANGUAGE_STOPWORDS[code])
        + 0.5 * sum(joined.count(marker) for marker in _SCRIPT_MARKERS.get(code, ()))
        for code in _LANGUAGE_ORDER
    }
    best_code = max(_LANGUAGE_ORDER, key=lambda c: scores[c])
    return best_code if scores[best_code] > 0 else None


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def _clean_messages(
    thread: SupportThread, config: Node3Config, stats: PreprocessingStats
) -> list[SupportMessage]:
    kept: list[SupportMessage] = []
    seen_customer: set[str] = set()
    for message in sorted(thread.messages, key=lambda m: (m.timestamp, m.message_id)):
        if message.role == "system":
            stats.n_system_messages_removed += 1
            continue
        if message.role == "customer":
            key = normalize_text(message.text)
            if key and key in seen_customer:
                stats.n_duplicate_messages_removed += 1
                continue
            seen_customer.add(key)
        kept.append(message)
    return kept[: config.max_messages_per_thread]


# --------------------------------------------------------------------------- #
# Cross-channel near-duplicate detection (§3.3 / Task 4.3)
# --------------------------------------------------------------------------- #
_KEY_ISSUE_PHRASES = (
    "cancel", "renew", "refund", "billing", "downgrade", "upgrade", "outage",
    "crash", "bug", "slow", "login", "password", "invoice", "overcharged",
    "competitor", "switch", "seat", "sso", "export", "integration",
)


def _first_customer_message(thread: SupportThread) -> str:
    for message in sorted(thread.messages, key=lambda m: (m.timestamp, m.message_id)):
        if message.role == "customer":
            return message.text
    return ""


def _candidate_text(thread: SupportThread) -> str:
    subject = thread.subject or ""
    return f"{subject} {_first_customer_message(thread)}".strip()


def _shared_key_phrases(a: str, b: str) -> bool:
    ta, tb = a.lower(), b.lower()
    return any(p in ta and p in tb for p in _KEY_ISSUE_PHRASES)


def _tfidf_cosine(docs: list[str]) -> list[list[float]]:
    """Pairwise TF-IDF cosine similarity for a small candidate set."""
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
    from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import-untyped]

    if len(docs) < 2:
        return []
    try:
        matrix = TfidfVectorizer().fit_transform(docs)
    except ValueError:
        return [[0.0] * len(docs) for _ in docs]
    rows: list[list[float]] = cosine_similarity(matrix).tolist()
    return rows


def _customer_token_count(thread: SupportThread) -> int:
    return sum(estimate_tokens(m.text) for m in thread.messages if m.role == "customer")


def _pick_survivor(t1: SupportThread, t2: SupportThread) -> tuple[SupportThread, SupportThread]:
    """Deterministic survivor: higher customer-token count, then earlier, then id."""
    n1, n2 = _customer_token_count(t1), _customer_token_count(t2)
    if n1 != n2:
        return (t1, t2) if n1 > n2 else (t2, t1)
    if t1.created_at != t2.created_at:
        return (t1, t2) if t1.created_at < t2.created_at else (t2, t1)
    return (t1, t2) if t1.thread_id < t2.thread_id else (t2, t1)


def _collapse_duplicates(
    threads: list[SupportThread], config: Node3Config, stats: PreprocessingStats
) -> list[PreprocessedThread]:
    by_customer: dict[str, list[SupportThread]] = defaultdict(list)
    for thread in threads:
        by_customer[thread.customer_id].append(thread)

    results: list[PreprocessedThread] = []
    for customer_id in sorted(by_customer):
        group = sorted(by_customer[customer_id], key=lambda t: (t.created_at, t.thread_id))
        by_id = {t.thread_id: t for t in group}
        collapsed: dict[str, str] = {}

        # (a) Honor explicit source-system ``duplicate_of`` annotations (§3.3).
        for thread in group:
            target = thread.duplicate_of
            if not target or target == thread.thread_id:
                continue
            seen = {thread.thread_id}
            while target in collapsed and target not in seen:
                seen.add(target)
                target = collapsed[target]
            if target in by_id and target != thread.thread_id:
                collapsed[thread.thread_id] = target

        # (b) Deterministic near-duplicate detection for the rest (§3.3).
        remaining = [t for t in group if t.thread_id not in collapsed]
        docs = [_candidate_text(t) for t in remaining]
        similarity = _tfidf_cosine(docs) if len(remaining) > 1 else []
        for i, t1 in enumerate(remaining):
            if t1.thread_id in collapsed:
                continue
            for j in range(i + 1, len(remaining)):
                t2 = remaining[j]
                if t2.thread_id in collapsed:
                    continue
                delta_seconds = abs((t1.created_at - t2.created_at).total_seconds())
                if delta_seconds > config.dedup_time_window_hours * 3600:
                    continue
                cosine = similarity[i][j] if similarity else 0.0
                if cosine < config.dedup_tfidf_threshold:
                    continue
                subject_ratio = SequenceMatcher(
                    None, normalize_text(t1.subject or ""), normalize_text(t2.subject or "")
                ).ratio()
                if subject_ratio < config.dedup_subject_threshold and not _shared_key_phrases(
                    docs[i], docs[j]
                ):
                    continue
                survivor, loser = _pick_survivor(t1, t2)
                collapsed[loser.thread_id] = survivor.thread_id
                if t1.thread_id in collapsed:
                    break

        stats.n_duplicates_collapsed += len(collapsed)
        for thread in group:
            results.append(
                PreprocessedThread(
                    thread=thread,
                    language=None,
                    language_status=LanguageStatus.UNKNOWN,
                    duplicate_of=collapsed.get(thread.thread_id),
                )
            )
    return results


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def preprocess_threads(
    threads: Sequence[SupportThread | dict[str, object]],
    config: Node3Config,
) -> tuple[list[PreprocessedThread], PreprocessingStats]:
    """Run the deterministic preprocessing pipeline (§3.3)."""
    stats = PreprocessingStats(n_input_threads=len(threads))
    in_window: list[SupportThread] = []

    for index, raw in enumerate(threads):
        if isinstance(raw, SupportThread):
            thread = raw
        else:
            try:
                thread = SupportThread.model_validate(raw)
            except ValidationError as exc:
                stats.n_dropped_invalid += 1
                stats.errors.append(
                    {"index": index, "code": "INVALID_THREAD", "detail": str(exc)}
                )
                continue
        if _is_blank(thread.customer_id):
            stats.n_dropped_invalid += 1
            stats.errors.append(
                {
                    "index": index,
                    "thread_id": thread.thread_id,
                    "code": "MISSING_CUSTOMER_ID",
                    "detail": "thread dropped: missing or blank customer_id",
                }
            )
            continue

        created = thread.created_at.date()
        age_days = (config.reference_date - created).days
        if created > config.reference_date or age_days > config.lookback_days:
            stats.n_out_of_window += 1
            continue

        cleaned = thread.model_copy(update={"messages": _clean_messages(thread, config, stats)})
        in_window.append(cleaned)

    preprocessed = _collapse_duplicates(in_window, config, stats)

    for item in preprocessed:
        code: str | None
        provided = item.thread.language
        if provided:
            code = provided
            if provided in config.supported_languages:
                item.language_status = LanguageStatus.SUPPORTED
            else:
                item.language_status = LanguageStatus.UNSUPPORTED
                stats.n_unsupported_language += 1
        else:
            code = detect_language(_customer_message_texts(item.thread))
            if code is None:
                item.language_status = LanguageStatus.UNKNOWN
                stats.n_unknown_language += 1
            elif code in config.supported_languages:
                item.language_status = LanguageStatus.SUPPORTED
            else:
                item.language_status = LanguageStatus.UNSUPPORTED
                stats.n_unsupported_language += 1
        item.language = code

    # Customer-level hard limit: keep the most recent non-collapsed threads.
    by_customer: dict[str, list[PreprocessedThread]] = defaultdict(list)
    for item in preprocessed:
        if item.duplicate_of is None:
            by_customer[item.thread.customer_id].append(item)
    kept_ids: set[str] = set()
    for customer_id in sorted(by_customer):
        group = sorted(
            by_customer[customer_id], key=lambda i: (i.thread.created_at, i.thread.thread_id)
        )
        limit = config.max_threads_per_customer
        if len(group) > limit:
            stats.n_threads_over_limit += len(group) - limit
            group = group[-limit:]
        kept_ids.update(i.thread.thread_id for i in group)

    final = [
        item
        for item in preprocessed
        if item.duplicate_of is not None or item.thread.thread_id in kept_ids
    ]
    final.sort(key=lambda i: (i.thread.customer_id, i.thread.created_at, i.thread.thread_id))
    return final, stats

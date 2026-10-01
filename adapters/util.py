"""Deterministic parsing helpers shared by adapters.

Everything here is pure and deterministic: same value -> same result, no timezone
or locale dependence. Unparseable values return ``None`` so the validation stage
can reject the affected record explicitly (the system is allowed to say "no").
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%b %d, %Y",
    "%d %b %Y",
)

# Status strings -> event_observed (architecture §1.6 example mapping).
_TRUE_STATUSES = {
    "churned",
    "churn",
    "canceled",
    "cancelled",
    "cancellation",
    "yes",
    "inactive",
    "closed",
    "true",
    "terminated",
    "lost",
    "customer-offboarding",
}
_FALSE_STATUSES = {
    "active",
    "no",
    "current",
    "open",
    "false",
    "subscribed",
    "trialing",
    "trialling",
    "past_due",
    "incomplete",
    "paused",
    "customer",
    "opportunity",
    "lead",
    "subscriber",
    "solved",
}


def _is_missing(value: object) -> bool:
    """None, or a pandas/numpy missing sentinel (NaN, NaT, pd.NA)."""
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN/NaT are the only values unequal to themselves
    except (TypeError, ValueError):  # pd.NA raises on bool()
        return True


def parse_date(value: object) -> date | None:
    """Parse a date from common formats. Returns ``None`` when unparseable.

    The whole text must be a date (or ISO datetime): trailing garbage is
    rejected rather than silently truncated. Slash dates are read US-style
    (``MM/DD/YYYY``); day-first data must use a mapping that says so.
    """
    # pandas yields NaT for blank Excel date cells; NaT is a datetime subclass,
    # so it must be caught before the datetime branch (it would crash tenure math).
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_float(value: object) -> float | None:
    """Coerce a value to a finite float, or ``None`` when it is not numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if "," in text:
            # Only a thousands separator may be dropped ("1,234.5"); "1,5" is a
            # decimal comma and would silently become 15 — treat it as unparseable.
            if not _THOUSANDS.match(text):
                return None
            text = text.replace(",", "")
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return number


def to_int(value: object) -> int | None:
    """Coerce a value to an int (no fractions, no booleans), or ``None``."""
    number = to_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def status_to_event(value: object) -> int | None:
    """Map a status string/bool to ``event_observed`` ({0, 1}), else ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number == 1.0:
            return 1
        if number == 0.0:
            return 0
        return None
    text = str(value).strip().lower()
    if text in _TRUE_STATUSES:
        return 1
    if text in _FALSE_STATUSES:
        return 0
    return None

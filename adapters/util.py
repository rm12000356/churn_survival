"""Deterministic parsing helpers shared by adapters.

Everything here is pure and deterministic: same value -> same result, no timezone
or locale dependence. Unparseable values return ``None`` so the validation stage
can reject the affected record explicitly (the system is allowed to say "no").
"""

from __future__ import annotations

import math
from datetime import date, datetime

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y")

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


def parse_date(value: object) -> date | None:
    """Parse a date from common formats. Returns ``None`` when unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    iso = text[:10]
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(iso)
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
        text = str(value).strip().replace(",", "")
        if not text:
            return None
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

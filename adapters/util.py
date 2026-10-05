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
    if value is None:
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return True


def parse_date(value: object) -> date | None:
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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if "," in text:
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
    number = to_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def status_to_event(value: object) -> int | None:
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

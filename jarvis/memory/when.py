"""Spoken time expressions -> datetime.

Pure by design: `now` is always injected so the tests are repeatable. Nothing
here reads the clock except the default argument.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

MORNING_HOUR = 9
EVENING_HOUR = 20

# "at 4", "at 4pm", "at 4:30", "at 16:00", "at 11:15am"
_CLOCK = re.compile(
    r"\bat\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\b", re.I
)
# "in 20 minutes", "in 2 hours", "in an hour", "in a minute"
_OFFSET = re.compile(
    r"\bin\s+(?P<n>\d+|an?|a)\s*(?P<unit>min(?:ute)?s?|hours?|hrs?)\b", re.I
)
_TOMORROW = re.compile(r"\btomorrow\b", re.I)
_TONIGHT = re.compile(r"\btonight\b", re.I)
_MORNING = re.compile(r"\bmorning\b", re.I)

# Everything the parser might consume, for strip_when.
_TIME_PHRASES = [
    re.compile(r"\btomorrow\s+morning\b", re.I),
    _CLOCK,
    _OFFSET,
    _TOMORROW,
    _TONIGHT,
    re.compile(r"\btonight\b", re.I),
]


def _resolve_hour(hour: int, ampm: str | None) -> int:
    """A bare '4' means the afternoon.

    Someone saying "remind me at 4" during a working day means 16:00. A 4am
    reminder would be a bug, not a feature. Only 8-11 stay in the morning.
    """
    if ampm:
        low = ampm.lower()
        if low == "pm" and hour < 12:
            return hour + 12
        if low == "am" and hour == 12:
            return 0
        return hour
    if hour <= 7:
        return hour + 12
    return hour


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Return the moment `text` refers to, or None if it names no time."""
    now = now or datetime.now()
    if not text:
        return None

    offset = _OFFSET.search(text)
    if offset:
        raw = offset.group("n").lower()
        n = 1 if raw in {"a", "an"} else int(raw)
        unit = offset.group("unit").lower()
        delta = timedelta(hours=n) if unit.startswith(("hour", "hr")) else timedelta(minutes=n)
        return (now + delta).replace(second=0, microsecond=0)

    tomorrow = bool(_TOMORROW.search(text))
    clock = _CLOCK.search(text)

    if clock:
        hour = _resolve_hour(int(clock.group("h")), clock.group("ampm"))
        minute = int(clock.group("m") or 0)
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow:
            return target + timedelta(days=1)
        # A time that has already passed means the next one.
        return target if target > now else target + timedelta(days=1)

    if tomorrow:
        base = (now + timedelta(days=1)).replace(
            hour=MORNING_HOUR, minute=0, second=0, microsecond=0
        )
        return base

    if _TONIGHT.search(text):
        return now.replace(hour=EVENING_HOUR, minute=0, second=0, microsecond=0)

    return None


def strip_when(text: str) -> str:
    """Remove the time phrase, leaving what the reminder is actually about."""
    out = text or ""
    for pattern in _TIME_PHRASES:
        out = pattern.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip(" ,.")

"""What Jarvis says when it wakes."""
from __future__ import annotations

from datetime import datetime

ADDRESS = "boss"

# Set False for a terse "Yes?" on every wake instead of the full greeting.
FULL_GREETING = True


def time_of_day(hour: int) -> str:
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def greeting(now: datetime | None = None) -> str:
    """'Good morning boss, how can I help you?'"""
    if not FULL_GREETING:
        return "Yes?"
    now = now or datetime.now()
    return f"{time_of_day(now.hour)} {ADDRESS}, how can I help you?"

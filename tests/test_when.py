from datetime import datetime

import pytest

from jarvis.memory.when import parse_when, strip_when

NOW = datetime(2026, 8, 15, 10, 30)  # a Saturday morning


@pytest.mark.parametrize(
    "text,expected",
    [
        ("at 4", datetime(2026, 8, 15, 16, 0)),
        ("at 4pm", datetime(2026, 8, 15, 16, 0)),
        ("at 4 pm", datetime(2026, 8, 15, 16, 0)),
        ("at 16:00", datetime(2026, 8, 15, 16, 0)),
        ("at 4:30", datetime(2026, 8, 15, 16, 30)),
        ("at 11am", datetime(2026, 8, 15, 11, 0)),
        ("at 11:15am", datetime(2026, 8, 15, 11, 15)),
    ],
)
def test_clock_times_today(text, expected):
    assert parse_when(text, NOW) == expected


def test_four_means_the_afternoon_not_4am():
    """A QA engineer saying 'remind me at 4' means 16:00. A 4am reminder
    would be a bug, not a feature."""
    assert parse_when("at 4", NOW).hour == 16


@pytest.mark.parametrize(
    "text,expected",
    [
        ("at 9am", datetime(2026, 8, 16, 9, 0)),   # 09:00 already passed
        ("at 10", datetime(2026, 8, 16, 10, 0)),   # 10:00 already passed
    ],
)
def test_a_time_already_past_rolls_to_tomorrow(text, expected):
    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("in 20 minutes", datetime(2026, 8, 15, 10, 50)),
        ("in 5 mins", datetime(2026, 8, 15, 10, 35)),
        ("in 2 hours", datetime(2026, 8, 15, 12, 30)),
        ("in an hour", datetime(2026, 8, 15, 11, 30)),
        ("in a minute", datetime(2026, 8, 15, 10, 31)),
    ],
)
def test_relative_offsets(text, expected):
    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tomorrow", datetime(2026, 8, 16, 9, 0)),
        ("tomorrow morning", datetime(2026, 8, 16, 9, 0)),
        ("tomorrow at 3", datetime(2026, 8, 16, 15, 0)),
        ("tonight", datetime(2026, 8, 15, 20, 0)),
    ],
)
def test_day_words(text, expected):
    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize("text", ["", "check the orders", "look at ZI-653", "remind me to do the thing"])
def test_no_time_returns_none(text):
    assert parse_when(text, NOW) is None


def test_a_card_id_is_not_mistaken_for_a_time():
    """'ZI-653' and 'MCSL 386' contain digits but are not clock times."""
    assert parse_when("check ZI-653", NOW) is None
    assert parse_when("look at MCSL 386", NOW) is None


def test_parse_when_defaults_to_the_real_clock():
    assert parse_when("in 5 minutes") is not None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("check ZI-653 orders at 4", "check ZI-653 orders"),
        ("call the team in 20 minutes", "call the team"),
        ("review the PR tomorrow morning", "review the PR"),
        ("do the thing", "do the thing"),
    ],
)
def test_strip_when_removes_the_time_phrase(text, expected):
    assert strip_when(text) == expected

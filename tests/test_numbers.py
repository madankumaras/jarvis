import pytest

from jarvis.correct.numbers import normalize_numbers


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("three eighty four", "384"),
        ("six five three", "653"),
        ("three hundred eighty four", "384"),
        ("six hundred fifty three", "653"),
        ("nineteen", "19"),
        ("eighty", "80"),
        ("zero", "0"),
        ("one two three four", "1234"),
    ],
)
def test_bare_numbers(spoken, expected):
    assert normalize_numbers(spoken) == expected


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("status of MCSL three eighty four", "status of MCSL 384"),
        ("what about ZI six five three", "what about ZI 653"),
        ("status of MCSL-384", "status of MCSL-384"),
        ("no numbers here at all", "no numbers here at all"),
    ],
)
def test_numbers_in_sentences(spoken, expected):
    assert normalize_numbers(spoken) == expected


def test_two_separate_runs_stay_separate():
    assert normalize_numbers("compare three eighty four and six five three") == (
        "compare 384 and 653"
    )


def test_existing_digits_pass_through_untouched():
    assert normalize_numbers("MCSL 384 and ZI 653") == "MCSL 384 and ZI 653"


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("three eighty four ", "384 "),
        ("three eighty four\n", "384\n"),
        ("six five three  ", "653  "),
    ],
)
def test_trailing_whitespace_after_a_run_is_preserved(spoken, expected):
    assert normalize_numbers(spoken) == expected

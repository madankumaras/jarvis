import pytest

from jarvis.correct.snap import correct
from jarvis.types import Vocab


@pytest.fixture
def vocab():
    return Vocab(
        cards=["MCSL-384", "MCSL-390"],
        people=["Ashok Kumar", "Madan Kumar"],
        carriers=["gls", "ups"],
        zi_ids=["ZI-653", "ZI-691"],
    )


@pytest.mark.parametrize(
    "heard",
    [
        "status of MCSL three eighty four",
        "status of muscle three eighty four",
        "status of M C S L 384",
        "status of mcsl384",
    ],
)
def test_card_id_snaps_to_real_card(heard, vocab):
    assert "MCSL-384" in correct(heard, vocab).text


def test_zi_id_snaps(vocab):
    assert "ZI-653" in correct("what about Z I six five three", vocab).text


def test_unknown_card_number_is_not_invented(vocab):
    # MCSL-999 is not in the vocabulary; do not snap it to a real card.
    result = correct("status of MCSL nine nine nine", vocab)
    assert "MCSL-384" not in result.text
    assert "MCSL-390" not in result.text


def test_surrounding_words_are_not_swallowed(vocab):
    # A greedy letter-run would eat "of" and yield "status MCSL-384".
    assert correct("status of MCSL three eighty four", vocab).text == "status of MCSL-384"


def test_near_miss_number_is_left_alone(vocab):
    # Digits 38 match no known card. Leave the text alone; never round to 384.
    result = correct("status of MCSL thirty eight", vocab)
    assert "MCSL-384" not in result.text
    assert "MCSL-390" not in result.text


def test_fuzzy_prefix_with_exact_digits_is_reported_as_ambiguous(vocab):
    # Digits match a real card, but the prefix is neither aliased nor close
    # enough to snap — this is exactly the 0.60-0.85 band.
    result = correct("status of MPQL 384", vocab)
    assert result.ambiguous == [("MPQL 384", "MCSL-384")]
    assert "MCSL-384" not in result.text


def test_text_without_entities_is_unchanged(vocab):
    assert correct("what are my tasks", vocab).text == "what are my tasks"


def test_empty_vocab_does_not_crash():
    assert correct("status of MCSL 384", Vocab()).text == "status of MCSL 384"

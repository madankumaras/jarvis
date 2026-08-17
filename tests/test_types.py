from jarvis.types import Response, Vocab, RpcError


def test_response_defaults():
    r = Response(speech="hello")
    assert r.speech == "hello"
    assert r.detail == ""
    assert r.tier == 1
    assert r.ok is True


def test_response_carries_detail_separately_from_speech():
    r = Response(speech="short", detail="a much longer body for the notification")
    assert r.speech != r.detail


def test_vocab_all_returns_every_entity():
    v = Vocab(cards=["MCSL-384"], people=["Ashok Kumar"], carriers=["gls"], zi_ids=["ZI-691"])
    assert set(v.all()) == {"MCSL-384", "Ashok Kumar", "gls", "ZI-691"}


def test_vocab_empty_by_default():
    assert Vocab().all() == []


def test_rpc_error_is_an_exception():
    assert issubclass(RpcError, Exception)

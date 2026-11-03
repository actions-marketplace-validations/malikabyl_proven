import json

import pytest

from good.bot import MRP, answer, ask_model, mrp_to_tenge, parse_reply
from proven_types import NotVerified


def test_mrp_to_tenge():
    assert mrp_to_tenge(10) == 10 * MRP.value


def test_ask_model_returns_model_text():
    assert ask_model("question", lambda q: "answer") == "answer"


def test_answer_sends_verified_text():
    sent = []
    answer("question", lambda q: "answer with a citation", lambda text: [], sent.append)
    assert sent == ["answer with a citation"]


def test_answer_blocks_unverified_text():
    sent = []
    with pytest.raises(NotVerified):
        answer("question", lambda q: "made-up answer", lambda text: ["the cited article is not in the database"], sent.append)
    assert sent == []


def test_parse_reply():
    assert parse_reply('{"a": 1}') == {"a": 1}
    with pytest.raises(json.JSONDecodeError):
        parse_reply("not json")

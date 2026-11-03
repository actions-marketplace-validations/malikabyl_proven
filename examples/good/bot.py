"""The same bot, written the way Proven requires."""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable

from proven_types import Fact, Unverified, unverified, verify

log = logging.getLogger(__name__)

# The value is taken from the law and signed with its source and date.
# After 400 days the judge itself will say it is time to check it again.
MRP = Fact(4325, source='Law of the Republic of Kazakhstan "On the Republican Budget '
                        'for 2026–2028" No. 239-VIII',
           date="2026-01-01")


def mrp_to_tenge(count: int) -> int:
    """Convert a fine from MRP (Kazakhstan's monthly index) to tenge."""
    return count * MRP.value


def ask_model(question: str, llm: Callable[[str], str]) -> Unverified:
    """Ask the model. The answer is marked unverified right away."""
    return unverified(llm(question))


def answer(question: str,
           llm: Callable[[str], str],
           checker: Callable[[str], Iterable[str]],
           send: Callable[[str], None]) -> None:
    """The client gets only what has passed the check."""
    raw = ask_model(question, llm)
    send(verify(raw, checker))


def parse_reply(text: str) -> dict:
    """The error is not swallowed: log it and re-raise."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("the model returned non-JSON: %.80s", text)
        raise

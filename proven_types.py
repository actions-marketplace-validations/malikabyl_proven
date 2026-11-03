"""Proven: types that make hallucinations visible.

Unverified  — what a model returned. Must not be sent to a person or written to a database.
Verified    — the same text after verify(). This is the only way to get one.
Fact        — a real-world value (an official index, a tax rate, an address) with a source and a date.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, NewType, TypeVar

T = TypeVar("T")

Unverified = NewType("Unverified", str)
Verified = NewType("Verified", str)


class NotVerified(Exception):
    """The model output did not pass verification."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = [p for p in problems if p]
        super().__init__("; ".join(self.problems) or "verification failed")


def unverified(text: str) -> Unverified:
    """Mark external text (model output, Dify, a third-party API) as unverified."""
    return Unverified(text)


def verify(text: Unverified, checker: Callable[[str], Iterable[str]]) -> Verified:
    """The only way to get Verified.

    checker returns the list of problems it found: an empty list means "verified".
    For example: every quote is in the database word for word, every cited article exists.
    """
    problems = [p for p in checker(text) if p]
    if problems:
        raise NotVerified(problems)
    return Verified(text)


@dataclass(frozen=True)
class Fact(Generic[T]):
    """A real-world value. Cannot be created without a source and a date."""

    value: T
    source: str
    date: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Fact without a source: say where the value comes from")
        try:
            _dt.date.fromisoformat(self.date)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Fact: date {self.date!r} is not in YYYY-MM-DD format") from exc

    def age_days(self, today: _dt.date | None = None) -> int:
        """Days since the source date."""
        return ((today or _dt.date.today()) - _dt.date.fromisoformat(self.date)).days

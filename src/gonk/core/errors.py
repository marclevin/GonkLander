"""Errors that are meant to be read by a human."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Status = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class Check:
    """One line of a diagnostic: what was checked, how it went, what to do."""

    name: str
    status: Status
    detail: str = ""
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "fail"


class GonkError(Exception):
    """An expected failure, with enough context to act on.

    `checks` shows what was tried; `hints` are commands or actions to try next.
    """

    def __init__(
        self,
        message: str,
        *,
        checks: Sequence[Check] = (),
        hints: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.checks = list(checks)
        self.hints = list(hints)

    def __str__(self) -> str:
        """The message and what to try, for places that can only show text."""
        if not self.hints:
            return self.message
        return self.message + "\n\nTry:\n" + "\n".join(f"    {hint}" for hint in self.hints)


def hints_from(checks: Iterable[Check]) -> list[str]:
    """Collect the hints of failing checks, in order, without duplicates."""
    seen: list[str] = []
    for check in checks:
        if check.status == "fail" and check.hint and check.hint not in seen:
            seen.append(check.hint)
    return seen

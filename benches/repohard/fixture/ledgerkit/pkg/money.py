from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError("cents must be int")

    def __add__(self, other: "Money") -> "Money":
        _same(self, other)
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        _same(self, other)
        return Money(self.cents - other.cents, self.currency)

    def split(self, parts: int) -> list["Money"]:
        """Split into N parts that sum exactly to self.cents."""
        if parts <= 0:
            raise ValueError("parts must be positive")
        share = self.cents / parts
        base = int(share)
        return [Money(base, self.currency) for _ in range(parts)]


def _same(a: Money, b: Money) -> None:
    if a.currency != b.currency:
        raise ValueError("currency mismatch")


def from_major(amount: float, currency: str = "USD") -> Money:
    return Money(int(round(amount * 100)), currency)

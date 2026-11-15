"""Money values and currency conversion.

Every amount is a ``Decimal`` quantised to two places. Floats are rejected on purpose:
``0.1 + 0.2`` is not a reimbursement amount anyone wants to explain to HR.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Union

CENT = Decimal("0.01")
Amount = Union[Decimal, int, str]


class CurrencyMismatch(ValueError):
    """Two Money values in different currencies were combined or compared."""


class UnknownCurrency(KeyError):
    """The FX table has no rate for this currency."""


def to_decimal(amount: Amount) -> Decimal:
    if isinstance(amount, float):
        raise TypeError("money must not be built from floats; pass a Decimal, int or str")
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def of(cls, amount: Amount, currency: str) -> "Money":
        return cls(to_decimal(amount), currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(Decimal(0), currency)

    def _check(self, other: object) -> "Money":
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")
        return other

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + self._check(other).amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.amount - self._check(other).amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        return self.amount < self._check(other).amount

    def __le__(self, other: "Money") -> bool:
        return self.amount <= self._check(other).amount

    def __gt__(self, other: "Money") -> bool:
        return self.amount > self._check(other).amount

    def __ge__(self, other: "Money") -> bool:
        return self.amount >= self._check(other).amount

    def scaled(self, factor: Amount) -> "Money":
        """Multiply by a plain number, e.g. ``cap.scaled("1.10")`` for a 10 % tolerance."""
        return Money(self.amount * Decimal(str(factor)), self.currency)

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"


def min_money(a: Money, b: Money) -> Money:
    a._check(b)
    return a if a.amount <= b.amount else b


class FxRates:
    """Rates against one base currency, used only for the management report.

    ``FxRates("NOK", {"INR": "0.125"})`` means 1 INR = 0.125 NOK. Payroll conversion is
    *not* our job: HR reimburses in the employee's payroll currency at its own rate.
    """

    def __init__(self, base: str, rates: Mapping[str, Amount], as_of: str = "") -> None:
        self.base = base.upper()
        self.as_of = as_of
        self._rates = {c.upper(): Decimal(str(r)) for c, r in rates.items()}
        self._rates[self.base] = Decimal(1)

    def rate(self, currency: str) -> Decimal:
        try:
            return self._rates[currency.upper()]
        except KeyError:
            raise UnknownCurrency(currency) from None

    def convert(self, money: Money, to: str) -> Money:
        to = to.upper()
        if money.currency == to:
            return money
        in_base = money.amount * self.rate(money.currency)
        return Money(in_base / self.rate(to), to)

    def describe(self, currency: str) -> str:
        return f"1 {currency.upper()} = {self.rate(currency):.4f} {self.base}"

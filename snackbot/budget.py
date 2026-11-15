"""The budget check.

Budgets live in the office's currency and so must the receipt: whoever you are, you pay in
rupees in Bengaluru. Currency conversion happens only in the management report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .model import Office, Receipt
from .money import CurrencyMismatch, Money, min_money
from .store import Repository


@dataclass(frozen=True)
class BudgetPolicy:
    overrun_tolerance_pct: int = 10  # a receipt up to cap × 1.10 is filed without asking a human


@dataclass(frozen=True)
class BudgetDecision:
    ok: bool
    reason: str
    cap: Money
    spent_before: Money
    remaining_after: Money


class BudgetService:
    def __init__(self, repo: Repository, policy: BudgetPolicy = BudgetPolicy()) -> None:
        self.repo = repo
        self.policy = policy

    def spent(self, office: Office, year: int, month: int) -> Money:
        total = Money.zero(office.currency)
        for p in self.repo.purchases(office.id, year, month):
            if p.reimbursable:
                total = total + p.total
        return total

    def remaining(self, office: Office, year: int, month: int) -> Money:
        return office.budget - self.spent(office, year, month)

    def spending_cap(self, office: Office, on: date) -> Money:
        """What one buyer may spend on a refill expected on ``on``: the smaller of what is left
        in that month's budget and a typical large shop."""
        remaining = self.remaining(office, on.year, on.month)
        if remaining.is_negative:
            return Money.zero(office.currency)
        return min_money(remaining, office.typical_max)

    def exhausted(self, office: Office, on: date) -> bool:
        """True when what is left would not even pay for a small refill."""
        return self.spending_cap(office, on) < office.typical_min

    def check_receipt(self, office: Office, cap: Money, receipt: Receipt) -> BudgetDecision:
        if receipt.total.currency != office.currency:
            raise CurrencyMismatch(
                f"receipts for {office.name} must be in {office.currency}, this one is in {receipt.total.currency}"
            )
        spent_before = self.spent(office, receipt.purchased_on.year, receipt.purchased_on.month)
        remaining_after = office.budget - spent_before - receipt.total
        tolerance = Decimal(1) + Decimal(self.policy.overrun_tolerance_pct) / Decimal(100)
        allowed = cap.scaled(tolerance)
        if receipt.total <= allowed:
            return BudgetDecision(True, "within the agreed cap", cap, spent_before, remaining_after)
        return BudgetDecision(
            False,
            f"{receipt.total} is more than {self.policy.overrun_tolerance_pct}% over the agreed cap of {cap}",
            cap, spent_before, remaining_after,
        )

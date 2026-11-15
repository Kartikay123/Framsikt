"""The monthly management report: one number across both offices, plus the detail behind it."""
from __future__ import annotations

from collections import Counter as Tally
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from .budget import BudgetService
from .model import OUT_OF_POCKET_STATES, Office, Refill
from .money import FxRates, Money
from .store import Repository

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


@dataclass
class OfficeReport:
    office: Office
    refills: int
    spent: Money
    budget: Money
    top_items: list[tuple[str, int]]
    by_buyer: list[tuple[str, int]]
    awaiting_hr: int
    refused: int

    @property
    def utilisation_pct(self) -> Decimal:
        if self.budget.amount == 0:
            return Decimal(0)
        return (self.spent.amount / self.budget.amount * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)

    @property
    def over_budget(self) -> bool:
        return self.spent > self.budget


@dataclass
class MonthlyReport:
    year: int
    month: int
    offices: list[OfficeReport]
    total: Money
    fx: FxRates

    def render(self, name_of: Callable[[str], str] = lambda s: s) -> str:
        lines = [f"📊 Snack spend — {MONTHS[self.month - 1]} {self.year}"]
        for o in self.offices:
            flag = "  ⚠️ OVER BUDGET" if o.over_budget else ""
            lines.append(
                f"{o.office.name}: {o.refills} refills, {o.spent} of {o.budget} ({o.utilisation_pct}%){flag}"
            )
            items = ", ".join(f"{item} ×{qty}" for item, qty in o.top_items) or "no line items recorded"
            buyers = ", ".join(f"{name_of(b)} ({n})" for b, n in o.by_buyer) or "nobody"
            lines.append(f"    most bought: {items}")
            lines.append(f"    buyers: {buyers}; awaiting HR: {o.awaiting_hr}; refused: {o.refused}")
        rates = "; ".join(self.fx.describe(o.office.currency) for o in self.offices
                          if o.office.currency != self.total.currency)
        as_of = f", rates as of {self.fx.as_of}" if self.fx.as_of else ""
        lines.append(f"Total: {self.total} ({rates}{as_of})" if rates else f"Total: {self.total}")
        return "\n".join(lines)


class Reporter:
    def __init__(self, repo: Repository, budget: BudgetService, fx: FxRates, reporting_currency: str) -> None:
        self.repo = repo
        self.budget = budget
        self.fx = fx
        self.currency = reporting_currency.upper()

    def monthly(self, year: int, month: int) -> MonthlyReport:
        reports = []
        total = Money.zero(self.currency)
        for office in self.repo.offices.values():
            purchases = self.repo.purchases(office.id, year, month)
            spent = self.budget.spent(office, year, month)
            items: Tally = Tally()
            buyers: Tally = Tally()
            for p in purchases:
                if not p.reimbursable:
                    continue
                buyers[p.buyer_id] += 1
                for line in p.lines:
                    items[line.item] += line.quantity

            def awaiting(r: Refill, office_id: str = office.id) -> bool:
                return (r.office_id == office_id and r.state in OUT_OF_POCKET_STATES
                        and r.receipt is not None
                        and (r.receipt.purchased_on.year, r.receipt.purchased_on.month) == (year, month))

            reports.append(OfficeReport(
                office=office,
                refills=sum(1 for p in purchases if p.reimbursable),
                spent=spent,
                budget=office.budget,
                top_items=items.most_common(3),
                by_buyer=buyers.most_common(),
                awaiting_hr=len(self.repo.refills(awaiting)),
                refused=sum(1 for p in purchases if not p.reimbursable),
            ))
            total = total + self.fx.convert(spent, self.currency)
        return MonthlyReport(year, month, reports, total, self.fx)

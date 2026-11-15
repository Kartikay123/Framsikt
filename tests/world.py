"""A small, fixed world the tests share: two offices, eight volunteers, two admins, a fake
chat, a fake HR system and a clock that starts on Monday 2 November 2026 at 09:00 Oslo time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from snackbot import (
    BudgetService, Counter, FakeChat, FakeClock, FakeHR, FxRates, Message, Office, OfficeHours,
    Person, Picker, Policy, Purchase, Receipt, ReceiptLine, Refill, RefillService, Reporter,
    Repository,
)

START = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)  # Monday; 09:00 in Oslo, 13:30 in Bengaluru

OSLO_VOLUNTEERS = [("anna", "Anna Berg"), ("bjorn", "Bjørn Dahl"), ("cecilie", "Cecilie Moe"), ("dag", "Dag Lie")]
BLR_VOLUNTEERS = [("priya", "Priya Nair"), ("rahul", "Rahul Shetty"), ("sneha", "Sneha Rao"), ("vikram", "Vikram Iyer")]


def oslo_office() -> Office:
    return Office("OSL", "Oslo", "NOK", Decimal(6000), (Decimal(300), Decimal(1200)),
                  OfficeHours("Europe/Oslo"), "#snacks-oslo", admins=("adm-oslo",))


def bengaluru_office() -> Office:
    return Office("BLR", "Bengaluru", "INR", Decimal(15000), (Decimal(500), Decimal(3000)),
                  OfficeHours("Asia/Kolkata"), "#snacks-blr", admins=("adm-blr",))


@dataclass
class World:
    clock: FakeClock
    repo: Repository
    chat: FakeChat
    hr: FakeHR
    fx: FxRates
    budget: BudgetService
    reporter: Reporter
    service: RefillService
    oslo: Office
    blr: Office

    def receipt(self, amount: str | int, currency: str, on: Optional[date] = None,
                lines: Sequence[ReceiptLine] = (), image: str = "receipt.jpg") -> Receipt:
        from snackbot import Money
        on = on or self.oslo.hours.local(self.clock.now()).date()
        return Receipt(Money.of(amount, currency), on, image, tuple(lines))

    def seed_purchase(self, office_id: str, buyer: str, on: date, amount: str | int,
                      lines: Sequence[ReceiptLine] = ()) -> Purchase:
        """Historic spend, e.g. imported from last month's receipts."""
        from snackbot import Money
        office = self.repo.offices[office_id]
        n = len(self.repo.purchases()) + 1
        return self.repo.add_purchase(
            Purchase(f"seed-{n}", office_id, buyer, on, Money.of(amount, office.currency), tuple(lines)))

    def ask_for(self, refill: Refill) -> Message:
        """The DM that asked the current nominee."""
        nominee = self.repo.get_refill(refill.id).nominee_id
        assert nominee, f"{refill.id} has no nominee"
        return next(m for m in reversed(self.chat.to_user(nominee)) if any(a.id == "accept" for a in m.actions))

    def accept(self, refill: Refill) -> Refill:
        ask = self.ask_for(refill)
        self.chat.click(ask.id, "accept", ask.to)
        return self.repo.get_refill(refill.id)

    def channel_post_with_button(self, refill: Refill, action_id: str = "volunteer") -> Message:
        office = self.repo.offices[refill.office_id]
        return next(m for m in self.chat.in_channel(office.channel)
                    if any(a.id == action_id and a.refill_id == refill.id for a in m.actions))

    def refill(self, refill_id: str) -> Refill:
        return self.repo.get_refill(refill_id)


def make_world(start: datetime = START, policy: Policy = Policy()) -> World:
    clock = FakeClock(start)
    repo = Repository()
    oslo = repo.add_office(oslo_office())
    blr = repo.add_office(bengaluru_office())
    repo.add_counter(Counter("OSL-1", "OSL", "Oslo snack counter"))
    repo.add_counter(Counter("BLR-1", "BLR", "Bengaluru snack counter"))
    for pid, name in OSLO_VOLUNTEERS:
        repo.add_person(Person(pid, name, "OSL", "NOK"))
    for pid, name in BLR_VOLUNTEERS:
        repo.add_person(Person(pid, name, "BLR", "INR"))
    repo.add_person(Person("adm-oslo", "Oslo office admin", "OSL", "NOK", volunteer=False))
    repo.add_person(Person("adm-blr", "Bengaluru office admin", "BLR", "INR", volunteer=False))

    fx = FxRates("NOK", {"INR": "0.125"}, as_of="2026-11-01")   # 1 INR = 0.125 NOK, i.e. 8 INR per NOK
    hr = FakeHR(clock, {p.id: p.payroll_currency for p in repo.people.values()}, fx)
    chat = FakeChat(clock)
    budget = BudgetService(repo)
    reporter = Reporter(repo, budget, fx, reporting_currency="NOK")
    service = RefillService(repo, chat, hr, clock, picker=Picker(repo), budget=budget,
                            reporter=reporter, policy=policy)
    return World(clock, repo, chat, hr, fx, budget, reporter, service, oslo, blr)

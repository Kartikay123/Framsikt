"""A scripted month at both offices, printed as the chat transcript people would have seen.

Run:  python3 -m snackbot.demo
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from snackbot import (
    BudgetService, Counter, DateRange, FakeChat, FakeClock, FakeHR, FxRates, Office, OfficeHours,
    Person, Picker, Receipt, ReceiptLine, RefillService, Reporter, Repository, Visit, Money,
)


def build():
    clock = FakeClock(datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))   # Monday 09:00 Oslo
    repo = Repository()
    repo.add_office(Office("OSL", "Oslo", "NOK", Decimal(6000), (Decimal(300), Decimal(1200)),
                           OfficeHours("Europe/Oslo"), "#snacks-oslo", admins=("hanne",)))
    repo.add_office(Office("BLR", "Bengaluru", "INR", Decimal(15000), (Decimal(500), Decimal(3000)),
                           OfficeHours("Asia/Kolkata"), "#snacks-blr", admins=("meera",)))
    repo.add_counter(Counter("OSL-1", "OSL", "Oslo snack counter"))
    repo.add_counter(Counter("BLR-1", "BLR", "Bengaluru snack counter"))
    for pid, name in [("anna", "Anna Berg"), ("bjorn", "Bjørn Dahl"), ("cecilie", "Cecilie Moe"), ("dag", "Dag Lie")]:
        repo.add_person(Person(pid, name, "OSL", "NOK"))
    for pid, name in [("priya", "Priya Nair"), ("rahul", "Rahul Shetty"), ("sneha", "Sneha Rao"), ("vikram", "Vikram Iyer")]:
        repo.add_person(Person(pid, name, "BLR", "INR"))
    repo.add_person(Person("hanne", "Hanne (Oslo office manager)", "OSL", "NOK", volunteer=False))
    repo.add_person(Person("meera", "Meera (Bengaluru office manager)", "BLR", "INR", volunteer=False))
    # Anna spends the second week of November in Bengaluru; Dag is only in on Thursdays and Fridays.
    repo.people["anna"].visits.append(Visit("BLR", DateRange(date(2026, 11, 9), date(2026, 11, 20))))
    repo.people["dag"].office_days = frozenset({3, 4})

    fx = FxRates("NOK", {"INR": "0.125"}, as_of="2026-11-01")
    hr = FakeHR(clock, {p.id: p.payroll_currency for p in repo.people.values()}, fx)
    chat = FakeChat(clock)
    budget = BudgetService(repo)
    reporter = Reporter(repo, budget, fx, "NOK")
    service = RefillService(repo, chat, hr, clock, picker=Picker(repo), budget=budget, reporter=reporter)
    return clock, repo, chat, hr, service, reporter


def say(clock, text):
    print(f"\n── {clock.now():%a %d %b %H:%M}Z  {text}")


def main() -> None:
    clock, repo, chat, hr, service, reporter = build()
    at = lambda d, h, m=0: clock.set(datetime(2026, 11, d, h, m, tzinfo=timezone.utc))

    say(clock, "Dag notices the Oslo counter is empty and presses the button.")
    oslo = service.report_empty("OSL-1", "dag", "both boxes empty")

    at(2, 9, 55); say(clock, "Anna is in a meeting and never answers. The scheduler runs every 5 minutes…")
    service.tick()
    say(clock, f"1h55 later: still waiting, nominee is {repo.get_refill(oslo.id).nominee_id!r}")
    at(2, 10, 5); service.tick()
    say(clock, f"Two office hours passed → handed to {repo.get_refill(oslo.id).nominee_id!r}")

    at(2, 10, 20); say(clock, "Bjørn accepts; a minute later Cecilie also presses “I'll buy it”.")
    ask = chat.last_to("bjorn")
    chat.click(ask.id, "accept", "bjorn")
    channel_post = chat.in_channel("#snacks-oslo")[0]
    chat.click(channel_post.id, "volunteer", "cecilie")

    at(3, 11, 0); say(clock, "Tuesday: Bjørn uploads the receipt. The claim goes to HR straight away.")
    service.submit_receipt(oslo.id, "bjorn", Receipt(
        Money.of("870", "NOK"), date(2026, 11, 3), "img://bjorn-2026-11-03.jpg",
        (ReceiptLine("nuts", 6, Decimal("75")), ReceiptLine("chocolate", 6, Decimal("45")), ReceiptLine("fruit", 10, Decimal("15"))),
    ))

    at(4, 6, 0); say(clock, "Wednesday 11:30 IST: Rahul reports the Bengaluru counter empty. Priya is asked and accepts.")
    blr = service.report_empty("BLR-1", "rahul")
    chat.click(chat.last_to(blr.nominee_id).id, "accept", blr.nominee_id)

    at(4, 12, 0); say(clock, "Priya spent 3,400 INR against a 3,000 INR cap: more than 10% over, so Meera has to approve.")
    service.submit_receipt(blr.id, "priya", Receipt(
        Money.of("3400", "INR"), date(2026, 11, 4), "img://priya-2026-11-04.jpg",
        (ReceiptLine("murukku", 10, Decimal("120")), ReceiptLine("biscuits", 20, Decimal("60")), ReceiptLine("fruit", 20, Decimal("50"))),
    ))
    at(4, 12, 30)
    chat.click(chat.last_to("meera").id, "approve_overrun", "meera")

    at(6, 9, 0); say(clock, "Friday: HR approves Bjørn's claim. The next scheduler run tells him.")
    hr.approve(repo.get_refill(oslo.id).claim_id)
    service.tick()

    at(9, 8, 0); say(clock, "Monday 9 Nov: Oslo is empty again. Anna is in Bengaluru this week, Bjørn just bought, Dag is only in Thu/Fri.")
    oslo2 = service.report_empty("OSL-1", "bjorn")
    chat.click(chat.last_to(oslo2.nominee_id).id, "accept", oslo2.nominee_id)
    at(9, 14, 0)
    service.submit_receipt(oslo2.id, oslo2.nominee_id, Receipt(
        Money.of("1150", "NOK"), date(2026, 11, 9), "img://cecilie-2026-11-09.jpg",
        (ReceiptLine("nuts", 8, Decimal("75")), ReceiptLine("chocolate", 10, Decimal("45")), ReceiptLine("crispbread", 4, Decimal("25")))))

    at(16, 13, 0); say(clock, "16 Nov: Priya's claim has sat in HR for 12 days. The admin is asked to chase it, once.")
    service.tick()
    at(18, 9, 0)
    hr.approve(repo.get_refill(blr.id).claim_id)
    hr.approve(repo.get_refill(oslo2.id).claim_id)
    service.tick()

    clock.set(datetime(2026, 12, 1, 7, 0, tzinfo=timezone.utc)); say(clock, "1 Dec: the month rolled over; the report goes to #management.")
    service.tick()

    print("\n\n================ CHAT TRANSCRIPT ================")
    print(chat.transcript())

    print("\n\n================ REFILLS ================")
    for r in repo.refills():
        print(f"{r.id}  {r.office_id}  {r.state.value:<12} buyer={r.buyer_id!s:<8} passed={r.passed}  claim={r.claim_id}")
        for e in r.history:
            print(f"      {e.at:%d %b %H:%M}Z  {e.type:<22} {e.actor or '':<8} {e.detail}")

    print("\n\n================ HR SYSTEM ================")
    for c in hr.claims.values():
        print(f"{c.id}  {c.employee_id:<8} {c.amount:>9} {c.currency}  {c.status.value:<9} payout={c.payout}  {c.description}")


if __name__ == "__main__":
    main()

# SnackBot — snack counter refill automation

Take-home for the Product Engineer role at Framsikt. Two offices (Oslo, Bengaluru), each with a
snack counter kept stocked by volunteers who pay out of pocket and claim the money back through
an external HR system. This repository contains the design, the core logic with simulated chat
and HR systems, and the tests that prove the rules.

| Deliverable | Where |
|---|---|
| Design document (≤ 2 pages) | [DESIGN.md](DESIGN.md) |
| Questions I would ask, and the assumptions made meanwhile | [QUESTIONS.md](QUESTIONS.md) |
| Core logic: refill lifecycle, choosing who buys, budget check | [`snackbot/`](snackbot/) |
| Simulated chat tool and HR system | [`snackbot/fakes.py`](snackbot/fakes.py) |
| Automated tests (58) | [`tests/`](tests/) |
| A scripted month, printed as the chat transcript | `python3 -m snackbot.demo` |

## Run it

Python 3.11 or newer, no third-party dependencies.

```bash
python3 -m unittest -v
```

```bash
python3 -m snackbot.demo
```

If you prefer pytest: `uv run --with pytest pytest -q` (or `pip install pytest && pytest -q`).

## What is where

```
snackbot/
  service.py      the refill state machine; every public method is a chat button or the scheduler
  picker.py       choosing who buys: at the office in the next two days, least recent buyer first
  budget.py       spending cap, receipt check, month totals per office in its own currency
  report.py       the monthly management report across both offices
  store.py        in-memory repository with the two invariants the database would enforce
  model.py        Office, Counter, Person, Refill, Receipt, Purchase (the ledger), states
  money.py        Decimal money that refuses to mix currencies; FX table for the report only
  officehours.py  office-hour arithmetic: a Friday 17:00 timeout expires Monday 09:00
  clock.py        injectable clock so tests can move time
  ports.py        the ChatPort and HRPort interfaces, and the chat Action (button)
  fakes.py        FakeChat (records messages, lets tests press buttons) and FakeHR (claims, approvals, outages)
  demo.py         a scripted November at both offices
tests/
  world.py             the fixed world the tests share (two offices, eight volunteers, two admins)
  test_lifecycle.py    empty → nominated → committed → purchased → claimed → reimbursed, and the failure paths
  test_commit_race.py  two people cannot both commit (sequential, and 28 threads at once)
  test_handover.py     a refill nobody completes is handed to someone else; timeouts in office hours
  test_picker.py       who gets asked, and who is skipped (away, visiting, busy, owed money)
  test_budget.py       caps, tolerance, admin approval, exhausted budget, currencies, the visitor case
  test_report.py       totals across NOK and INR, utilisation, awaiting/refused, month roll-over
  test_officehours.py  office-hour arithmetic
  test_money.py        exact decimals, no currency mixing, FX
```

## The rules the tests prove

- **Two people cannot both commit to the same refill.** `test_commit_race.py`: the second button
  press is turned away and told who has it; 28 threads racing for one refill produce exactly one
  buyer; five simultaneous "it's empty" reports open one refill; the store rejects stale writes.
- **A refill nobody completes is handed to someone else.** `test_handover.py`: no answer in two
  office hours, a decline, a hand-back or a missed purchase deadline each pass the refill to the next
  person; people who passed are not asked again; when everyone has passed, the office is alerted once
  and anyone can still volunteer.
- **Budgets and totals are correct across both currencies.** `test_budget.py`, `test_report.py`,
  `test_money.py`: each office budget is in its own currency, a receipt must be in the office currency,
  an Oslo employee visiting Bengaluru spends INR against the Bengaluru budget and is paid in NOK by HR,
  the cap is min(remaining, typical large shop), over-cap receipts need an admin, the month is decided by
  the receipt date, and the report totals 1,450.50 NOK + 4,000 INR as 1,950.50 NOK at the stated rate.
- **The HR hand-off is safe.** `test_lifecycle.py`: the claim is filed with the right employee id,
  amount, currency, description and receipt; an HR outage is retried without creating a second claim;
  rejections and slow approvals (> 10 days) reach the admin exactly once.

## How the simulation maps to the real thing

| In the code | In production |
|---|---|
| `FakeChat.post / dm / click` | the chat tool's API and its button webhooks calling `RefillService.handle_action` |
| `FakeHR.create_claim / claim_status` | the HR vendor's API, with our refill id as the claim reference |
| `Repository` (dicts + lock, version check, one open refill per counter) | Postgres: a partial unique index and a `version` column checked on every update |
| `RefillService.tick()` | a cron job every five minutes |
| `FakeClock` | the system clock |

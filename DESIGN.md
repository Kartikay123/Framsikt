# Snack Counter Automation — Design

**What "automate" means here.** A bot in the company chat tool, backed by a small service that is the single source of truth for every refill: who is buying, by when, for how much, and where the reimbursement stands. It names a buyer, locks the refill to exactly one person, files the HR claim the moment a receipt is uploaded, chases HR when it is slow, and writes the monthly report. People still buy the snacks; the bot removes the coordination and the paperwork.

## 1. A refill, from "empty" to "reimbursed"

1. **Reported.** Anyone presses "box is empty" (a button in the office snack channel, or a QR code on the counter). If a refill is already open for that counter, the reporter is told who is on it.
2. **Nominated.** The service picks one volunteer and sends a direct message: *"Could you refill by Tuesday? Spend up to 1,200 NOK."* They have two **office** hours to answer, so a Friday 17:30 nomination expires Monday 09:00. The channel post has an "I'll buy it" button for anyone going shopping anyway.
3. **Committed.** Exactly one person holds the refill. The commit is an optimistic-locked write: a second person pressing the button, even in the same millisecond, is told "Bjørn already has this one". The buyer gets a deadline (end of the next office day), a spending cap, and a reminder three hours before it.
4. **Purchased.** The buyer uploads the receipt (amount, date, photo, optional line items) in the same conversation. The spend hits the ledger immediately: the money is already gone. Budget check: within the agreed cap plus 10 % → continue; over that → the office admin gets Approve / Refuse buttons.
5. **Claimed.** The service creates the HR claim itself: amount, currency, description with the refill id, receipt image, employee id. The buyer never opens the HR system. If HR is down the scheduler retries, and the refill id is the claim reference, so a retry can never create a second claim.
6. **Reimbursed.** The scheduler polls the claim. Approved → the buyer is told and the refill closes. Rejected → buyer and admin are told. Pending for more than 10 days → the admin is asked to chase, once.

Anything not completed is passed on. An unanswered nomination, a decline, a handed-back commitment or a missed deadline each put the refill back to *reported* and nominate the next person. People who passed are not asked again for that refill, but may still volunteer. When nobody is available, or the month's budget is used up, channel and admin are told once and the scheduler keeps retrying as availability or the month changes.

## 2. The parts, and the data each keeps

| Part | Does | Owns |
|---|---|---|
| **Chat adapter** | Renders messages and buttons; turns clicks into service calls; maps chat identity to employee id via the company login | nothing durable |
| **Refill service** | The state machine above and every rule in it | `refills`: counter, state, `version`, reporter, nominee and expiry, buyer, deadline, cap, receipt, claim id, retry count, who passed, event history |
| **Picker** | Decides whom to ask | reads `people` (home office, payroll currency, weekly office days, absences, visits) plus refill and ledger history |
| **Budget and ledger** | Spending cap, receipt check, totals per office and month | `purchases`: refill, office, buyer, receipt date, total in office currency, line items, reimbursable flag |
| **HR adapter** | Creates claims, reads status | claim id ↔ refill id, filed-at, last status |
| **Reporter** | The monthly report across offices | reads ledger, `offices` (currency, budget), an FX table with its as-of date |
| **Scheduler** | Runs `tick()` every five minutes: expiries, reminders, retries, HR polling, month roll-over | the last month reported |

Production shape: one small Python service with Postgres, the chat tool's webhooks for buttons, a cron job for `tick`, HTTPS to the HR vendor. The two invariants the design leans on are database constraints, not application discipline: a partial unique index for "one open refill per counter" and a `version` column checked on every update. The in-memory repository here enforces both under a lock, and the tests race it with threads.

## 3. How the four problems are prevented

- **The box sits empty for days.** Somebody is *named*, with a deadline, seconds after the report. Silence is handled by the clock, not by goodwill: no answer in two office hours → next person; no receipt by the deadline → next person. The channel always shows who is on it.
- **Two people buy on the same day.** One open refill per counter and one committed buyer, enforced by the store. Reporting "empty" twice does not open a second refill. Late pressers are told who has it.
- **A few people do most refills and wait weeks.** The picker ranks by longest time since last purchase, then fewest recent purchases, and skips anyone already waiting on two reimbursements, so the money float is spread and visible. The claim is filed the moment the receipt arrives, not when the volunteer gets round to it, and HR is chased after ten days.
- **Nobody knows what is spent or eaten.** Every receipt becomes a ledger row in the office currency. The report shows, per office, refills, spend against budget, utilisation, buyers and the most bought items, and one combined total in NOK at a stated rate.

## 4. Trade-offs, and what I would deliberately not build

- **Nominate one person rather than broadcast.** Broadcast recreates "someone else will do it". A named person with a short fallback chain fixes that, and the "I'll buy it" button keeps the fast path.
- **Budget as a cap before buying, not a refusal after.** A volunteer who has already paid should never be refused over a rounding error, so receipts up to 10 % over the cap are filed. Beyond that a human decides; the alternatives are silently blowing the budget or punishing goodwill. The cap is the smaller of what is left this month and a typical large shop.
- **Receipts in the office currency; HR converts.** Budgets are per office in local currency because that is what gets spent. An Oslo employee in Bengaluru claims INR and HR pays NOK, as today. The report converts with a monthly rate table and prints the rate.
- **Availability is self-declared.** Office days, holidays and visits are a few bot commands; calendar integration can come later.
- **Timeouts in office hours.** A little code, and nominations stop expiring on Friday night.
- **Not building:** inventory tracking (sensors, scales, stock counts; what gets bought answers "what gets eaten" well enough), a web or mobile UI (the chat tool is the UI), a company card or any payment flow (reimbursement exists and is HR's job), approval chains beyond one admin button, demand forecasting, or any replacement of HR's own approval. Also not yet: a grace period for a buyer who bought but missed the upload deadline; today the admin handles that by hand.

## 5. Assumptions

HR returns a claim id and exposes status, and a repeated reference does not create a second claim. The chat tool has channels, direct messages and buttons, and chat identity maps to the HR employee id. Budgets are per office per calendar month. Visitors may buy for the host office. The questions behind these, and the assumption each rests on, are in `QUESTIONS.md`.

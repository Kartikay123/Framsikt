# Questions I would ask before building — and the assumptions I made meanwhile

The brief says asking questions is part of the exercise. These are the ones whose answers would
change the design. Each comes with the assumption the submitted code makes, so nothing was blocked.

| # | Question | Assumption in the code |
|---|---|---|
| 1 | Does the HR API return a claim id, and can we read a claim's status (pending / approved / rejected)? Is there a webhook, or do we poll? | It returns an id and exposes status; we poll from the scheduler. |
| 2 | Does the HR API accept an idempotency key, or a free-text reference we can search by, so a retried request never creates two claims? | We send our refill id as the reference and the HR side treats a repeated reference as the same claim. If the real API cannot, we record "claim filed" *before* confirming and reconcile on failure. |
| 3 | Can an employee claim in a currency other than their payroll currency (an Oslo employee buying in INR while visiting Bengaluru)? Who converts, at what rate? | Yes; the claim is in the receipt currency and HR converts to payroll currency. Our system never converts for reimbursement. |
| 4 | Is the monthly budget a hard stop or a target? Who may approve going over, and by how much? | A cap before buying (never more than what is left, never more than a typical large shop). Receipts up to 10 % over the agreed cap are filed; beyond that the office admin approves or refuses. |
| 5 | Is it one snack counter per office, or several boxes that may run out separately? Are refills "whatever is empty" or per box? | One refill covers a counter; the reporter can add a note ("nuts and fruit"). A second counter is one line of configuration. |
| 6 | Which chat tool is it, and does it support direct messages, channel posts, buttons and webhooks? Can we map chat identity to the HR employee id (SSO)? | Slack/Teams-like: yes to all; identity comes from the company login. |
| 7 | How do we learn who is at which office on which day — self-declared, HR calendar, badge data? | Self-declared in the bot: weekly office days, holidays, visits. Calendar integration is a later improvement. |
| 8 | Which currency does management want the combined monthly figure in, and whose FX rate? | NOK, with a monthly rate table whose as-of date is printed on the report. |
| 9 | Who are the office admins (approve overruns, receive escalations, can cancel a false alarm)? | One admin per office, configured. |
| 10 | Are visiting employees allowed, and expected, to buy for the host office? | Allowed and included in the rotation while they are there. |
| 11 | Should the buyer's history carry across offices (a refill done in Bengaluru counts towards "has done their share" in Oslo)? | Yes: fairness is per person, not per office. |
| 12 | What happens to receipts a buyer uploads *after* missing the deadline, when the refill has already been passed on? | Out of scope for the bot; the admin handles it manually. A grace period ("did you already buy?") is the obvious next feature. |
| 13 | Any constraints on the receipt image (original required, size, retention), and does HR need the line items? | A photo reference is enough for HR; line items are optional and only feed the report. |

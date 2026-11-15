"""The refill lifecycle.

Every public method is something a chat button, a chat command or the scheduler calls.
The service owns the state machine; the chat tool and the HR system are behind ports.

    REPORTED ──nominate──▶ NOMINATED ──accept/volunteer──▶ COMMITTED ──receipt──▶ PURCHASED ──file──▶ CLAIMED ──HR ok──▶ REIMBURSED
        ▲                      │ decline / timeout             │ hand back / deadline     └─over cap─▶ NEEDS_APPROVAL ─approve─▶ PURCHASED
        └──────────────────────┴───────────────────────────────┘                                                      └─refuse──▶ REJECTED
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .budget import BudgetService
from .clock import Clock
from .model import Office, Purchase, Receipt, Refill, RefillState
from .money import CurrencyMismatch
from .picker import Picker
from .ports import Action, ChatPort, HRPort, HRRejectedRequest, HRUnavailable
from .report import MONTHS, Reporter
from .store import ConflictError, DuplicateOpenRefill, Repository

S = RefillState


@dataclass(frozen=True)
class Policy:
    nomination_timeout_hours: float = 2.0      # office hours a nominee has to answer
    purchase_window_days: int = 2              # office days a buyer has to shop; today counts if the office is open
    reminder_hours_before_deadline: float = 3.0
    hr_chase_after_days: int = 10              # HR promises 1–10 days; after that we ask what is going on
    max_claim_attempts: int = 5                # HR outage retries before an admin is told
    report_channel: str = "#management"


class SnackError(Exception):
    """A rule was violated. The chat layer turns these into a polite message, never a crash."""


class AlreadyCommitted(SnackError):
    def __init__(self, buyer_id: Optional[str]) -> None:
        super().__init__(f"already taken by {buyer_id}")
        self.buyer_id = buyer_id


class InvalidState(SnackError):
    pass


class NotAllowed(SnackError):
    pass


class InvalidReceipt(SnackError):
    pass


class RefillService:
    def __init__(
        self,
        repo: Repository,
        chat: ChatPort,
        hr: HRPort,
        clock: Clock,
        *,
        picker: Optional[Picker] = None,
        budget: Optional[BudgetService] = None,
        reporter: Optional[Reporter] = None,
        policy: Policy = Policy(),
    ) -> None:
        self.repo = repo
        self.chat = chat
        self.hr = hr
        self.clock = clock
        self.picker = picker or Picker(repo)
        self.budget = budget or BudgetService(repo)
        self.reporter = reporter
        self.policy = policy
        self._last_report_month = self._month(clock.now())
        if hasattr(chat, "on_action"):          # let the (fake) chat route button clicks to us
            chat.on_action = self.handle_action

    # ======================================================================================
    # Inbound: things people do in the chat tool
    # ======================================================================================
    def report_empty(self, counter_id: str, reporter_id: str, note: str = "") -> Refill:
        """Anyone presses the 'box is empty' button (or scans the QR code on the counter)."""
        counter = self.repo.counters[counter_id]
        office = self.repo.offices[counter.office_id]
        existing = self.repo.open_refill_for(counter_id)
        refill: Optional[Refill] = None
        if existing is None:
            now = self.clock.now()
            refill = Refill(self.repo.new_refill_id(), counter_id, office.id, reporter_id, now, note)
            refill.record(now, "reported", reporter_id, note)
            try:
                self.repo.insert_refill(refill)
            except DuplicateOpenRefill as dup:   # two people reported within the same instant
                existing, refill = dup.existing, None
        if existing is not None:
            self.chat.dm(reporter_id, self._already_in_progress(existing))
            return existing
        assert refill is not None
        self.chat.post(
            office.channel,
            f"🥨 The {counter.name} is empty (reported by {self._name(reporter_id)}). Finding a buyer…",
            [Action("volunteer", "I'll buy it", refill.id)],
        )
        self._nominate(refill)
        return refill

    def commit(self, refill_id: str, user_id: str) -> Refill:
        """The nominee accepts, or anyone presses 'I'll buy it'. Exactly one person wins."""
        if user_id not in self.repo.people:
            raise NotAllowed(f"{user_id} is not a known employee")
        for _ in range(3):   # optimistic locking: reload and retry on a benign conflict
            refill = self.repo.get_refill(refill_id)
            self._must_be_claimable(refill)
            office = self.repo.offices[refill.office_id]
            now = self.clock.now()
            days = office.hours.purchase_days(now, self.policy.purchase_window_days)
            previous_nominee = refill.nominee_id if refill.nominee_id != user_id else None
            if refill.nominee_id != user_id or refill.spending_cap is None:
                # a self-volunteer gets today's cap; the nominee keeps the cap they were promised
                refill.spending_cap = self.budget.spending_cap(office, days[0])
            refill.state = S.COMMITTED
            refill.buyer_id = user_id
            refill.committed_at = now
            refill.purchase_deadline = office.hours.closing(days[-1]).astimezone(timezone.utc)
            refill.nominee_id = None
            refill.nomination_expires_at = None
            refill.record(now, "committed", user_id)
            try:
                self.repo.save_refill(refill)
                break
            except ConflictError:
                continue
        else:
            self._must_be_claimable(self.repo.get_refill(refill_id))
            raise ConflictError(f"could not commit {refill_id}, please try again")

        counter = self.repo.counters[refill.counter_id]
        deadline = office.hours.fmt(refill.purchase_deadline)
        self.chat.post(
            office.channel,
            f"✅ {self._name(user_id)} is buying for the {counter.name}. Cap {refill.spending_cap}, by {deadline}.",
        )
        self.chat.dm(
            user_id,
            f"You're on for the {counter.name}. Spend up to {refill.spending_cap} and upload the receipt "
            f"here by {deadline}. I'll file the HR claim the moment it arrives.",
            [Action("hand_back", "I can't after all", refill.id)],
        )
        if previous_nominee:
            self.chat.dm(previous_nominee,
                         f"{self._name(user_id)} took the {counter.name} refill — nothing needed from you.")
        return refill

    def decline(self, refill_id: str, user_id: str, reason: str = "") -> Refill:
        refill = self.repo.get_refill(refill_id)
        if refill.state != S.NOMINATED or refill.nominee_id != user_id:
            raise NotAllowed("only the person currently asked can decline")
        self._pass(refill, user_id, "declined", reason)
        self._nominate(refill)
        return refill

    def hand_back(self, refill_id: str, user_id: str, reason: str = "") -> Refill:
        refill = self.repo.get_refill(refill_id)
        if refill.state != S.COMMITTED or refill.buyer_id != user_id:
            raise NotAllowed("only the committed buyer can hand a refill back")
        office, counter = self._office_and_counter(refill)
        self._pass(refill, user_id, "handed_back", reason)
        self.chat.post(office.channel,
                       f"↩️ {self._name(user_id)} can't do the {counter.name} refill after all. Finding someone else…")
        self._nominate(refill)
        return refill

    def submit_receipt(self, refill_id: str, user_id: str, receipt: Receipt) -> Refill:
        """The buyer uploads the receipt. Money is now spent, so it always lands in the ledger;
        whether the claim is filed straight away depends on the budget check."""
        refill = self.repo.get_refill(refill_id)
        if refill.state != S.COMMITTED:
            raise InvalidState(f"{refill.id} is {refill.state.value}, not waiting for a receipt")
        if refill.buyer_id != user_id:
            raise NotAllowed("only the committed buyer can submit the receipt")
        office, counter = self._office_and_counter(refill)
        assert refill.spending_cap is not None
        try:
            decision = self.budget.check_receipt(office, refill.spending_cap, receipt)
        except CurrencyMismatch as e:
            self.chat.dm(user_id, f"❌ {e}. Please upload the shop receipt in {office.currency}.")
            raise InvalidReceipt(str(e)) from e

        now = self.clock.now()
        refill.receipt = receipt
        refill.purchased_at = now
        self.repo.add_purchase(Purchase(refill.id, office.id, user_id, receipt.purchased_on,
                                        receipt.total, receipt.lines))
        if decision.ok:
            refill.state = S.PURCHASED
            refill.record(now, "receipt_accepted", user_id, decision.reason)
            self.repo.save_refill(refill)
            self._file_claim(refill)
        else:
            refill.state = S.NEEDS_APPROVAL
            refill.record(now, "receipt_over_cap", user_id, decision.reason)
            self.repo.save_refill(refill)
            self.chat.dm(user_id, f"Receipt received ({receipt.total}). That is over the agreed cap "
                                  f"of {refill.spending_cap}, so an office admin has to approve it "
                                  f"before I can file the claim. I've asked them.")
            for admin in office.admins:
                self.chat.dm(
                    admin,
                    f"⚠️ {self._name(user_id)} spent {receipt.total} on the {counter.name}: {decision.reason}. "
                    f"{office.name} would have {decision.remaining_after} left this month.",
                    [Action("approve_overrun", "Approve & file claim", refill.id),
                     Action("reject_overrun", "Refuse", refill.id)],
                )
        return refill

    def approve_overrun(self, refill_id: str, admin_id: str) -> Refill:
        refill = self.repo.get_refill(refill_id)
        office, _ = self._office_and_counter(refill)
        self._must_be_admin(office, admin_id)
        if refill.state != S.NEEDS_APPROVAL:
            raise InvalidState(f"{refill.id} is not waiting for approval")
        refill.state = S.PURCHASED
        refill.record(self.clock.now(), "overrun_approved", admin_id)
        self.repo.save_refill(refill)
        self._file_claim(refill)
        return refill

    def reject_overrun(self, refill_id: str, admin_id: str, reason: str) -> Refill:
        refill = self.repo.get_refill(refill_id)
        office, counter = self._office_and_counter(refill)
        self._must_be_admin(office, admin_id)
        if refill.state != S.NEEDS_APPROVAL:
            raise InvalidState(f"{refill.id} is not waiting for approval")
        now = self.clock.now()
        refill.state = S.REJECTED
        refill.closed_at = now
        refill.record(now, "overrun_refused", admin_id, reason)
        self.repo.save_refill(refill)
        self.repo.set_reimbursable(refill.id, False)
        assert refill.buyer_id
        self.chat.dm(refill.buyer_id, f"{self._name(admin_id)} did not approve the {counter.name} purchase "
                                      f"of {refill.receipt.total if refill.receipt else ''}: {reason}. "
                                      f"Please talk to them directly.")
        return refill

    def cancel(self, refill_id: str, admin_id: str, reason: str = "") -> Refill:
        """False alarm, or the box got filled some other way. Only before anyone has paid."""
        refill = self.repo.get_refill(refill_id)
        office, counter = self._office_and_counter(refill)
        self._must_be_admin(office, admin_id)
        if not refill.is_open:
            raise InvalidState("only refills nobody has paid for yet can be cancelled")
        affected = refill.buyer_id or refill.nominee_id
        now = self.clock.now()
        refill.state = S.CANCELLED
        refill.closed_at = now
        refill.record(now, "cancelled", admin_id, reason)
        self.repo.save_refill(refill)
        self.chat.post(office.channel, f"🚫 The {counter.name} refill was cancelled by {self._name(admin_id)}: {reason}")
        if affected:
            self.chat.dm(affected, f"The {counter.name} refill was cancelled ({reason}). Nothing to do.")
        return refill

    def handle_action(self, user_id: str, action: Action) -> str:
        """Entry point for button clicks from the chat tool."""
        try:
            if action.id in ("volunteer", "accept"):
                refill = self.commit(action.refill_id, user_id)
                return f"committed:{refill.buyer_id}"
            if action.id == "decline":
                self.decline(action.refill_id, user_id)
                return "declined"
            if action.id == "hand_back":
                self.hand_back(action.refill_id, user_id)
                return "handed_back"
            if action.id == "approve_overrun":
                self.approve_overrun(action.refill_id, user_id)
                return "approved"
            if action.id == "reject_overrun":
                self.reject_overrun(action.refill_id, user_id, "refused via chat")
                return "refused"
            raise NotAllowed(f"unknown action {action.id}")
        except AlreadyCommitted as e:
            self.chat.dm(user_id, f"Too late — {self._name(e.buyer_id or '?')} already has this one. Thanks anyway!")
            return f"rejected:{e}"
        except SnackError as e:
            self.chat.dm(user_id, f"Sorry, I can't do that: {e}")
            return f"rejected:{e}"

    # ======================================================================================
    # Scheduled: the one method a cron job calls every few minutes
    # ======================================================================================
    def tick(self) -> list[str]:
        now = self.clock.now()
        log: list[str] = []
        for refill in self.repo.refills(lambda r: r.is_active):
            office, counter = self._office_and_counter(refill)

            if refill.state == S.NOMINATED:
                assert refill.nomination_expires_at and refill.nominee_id
                if now >= refill.nomination_expires_at:
                    nominee = refill.nominee_id
                    self._pass(refill, nominee, "nomination_expired")
                    self.chat.dm(nominee, f"No answer about the {counter.name} refill, so I've asked someone else. No worries.")
                    log.append(f"{refill.id}: {nominee} did not answer, passing on")
                    self._nominate(refill)

            elif refill.state == S.REPORTED:          # blocked earlier (nobody around / no budget); try again
                if self._nominate(refill):
                    log.append(f"{refill.id}: nominated {refill.nominee_id}")

            elif refill.state == S.COMMITTED:
                assert refill.purchase_deadline and refill.buyer_id
                if now >= refill.purchase_deadline:
                    buyer = refill.buyer_id
                    self._pass(refill, buyer, "deadline_missed")
                    self.chat.dm(buyer, f"The deadline for the {counter.name} refill passed, so I've handed it to "
                                        f"someone else. If you did buy something, please tell an office admin.")
                    self.chat.post(office.channel, f"⏰ {self._name(buyer)} didn't get to the {counter.name} refill in time. Finding someone else…")
                    log.append(f"{refill.id}: {buyer} missed the deadline, passing on")
                    self._nominate(refill)
                elif (not refill.reminder_sent
                      and now >= refill.purchase_deadline - timedelta(hours=self.policy.reminder_hours_before_deadline)):
                    refill.reminder_sent = True
                    refill.record(now, "reminded", refill.buyer_id)
                    self.repo.save_refill(refill)
                    self.chat.dm(refill.buyer_id, f"Reminder: the {counter.name} refill is due by "
                                                  f"{office.hours.fmt(refill.purchase_deadline)}. Still on it?",
                                 [Action("hand_back", "I can't after all", refill.id)])
                    log.append(f"{refill.id}: reminded {refill.buyer_id}")

            elif refill.state == S.PURCHASED:         # a claim we could not file earlier (HR was down)
                if self._file_claim(refill):
                    log.append(f"{refill.id}: claim filed on retry")

            elif refill.state == S.CLAIMED:
                log.extend(self._follow_up_claim(refill, office, counter.name, now))

        log.extend(self._post_report_if_month_rolled(now))
        return log

    # ======================================================================================
    # Internals
    # ======================================================================================
    def _nominate(self, refill: Refill) -> bool:
        """Pick one person and ask them. Returns True if somebody was asked."""
        office, counter = self._office_and_counter(refill)
        now = self.clock.now()
        days = office.hours.purchase_days(now, self.policy.purchase_window_days)

        if self.budget.exhausted(office, days[0]):
            spent = self.budget.spent(office, days[0].year, days[0].month)
            self._alert_once(refill, office, "budget",
                             f"🧮 {office.name}'s snack budget for {MONTHS[days[0].month - 1]} is used up "
                             f"({spent} of {office.budget}). No refill until next month unless an admin raises it.")
            return False

        candidate = self.picker.pick(office, days, exclude=refill.passed)
        if candidate is None:
            self._alert_once(refill, office, "no_candidate",
                             f"🙋 Nobody on the volunteer list is at {office.name} in the next "
                             f"{len(days)} office days, or everyone has already passed. Can anyone buy? "
                             f"Press “I'll buy it” above.")
            return False

        refill.state = S.NOMINATED
        refill.nominee_id = candidate.id
        refill.nomination_expires_at = office.hours.add_office_hours(
            now, self.policy.nomination_timeout_hours).astimezone(timezone.utc)
        refill.spending_cap = self.budget.spending_cap(office, days[0])
        refill.record(now, "nominated", candidate.id)
        self.repo.save_refill(refill)
        self.chat.dm(
            candidate.id,
            f"Hi {candidate.name.split()[0]} — could you refill the {counter.name} by {days[-1]:%A}? "
            f"Spend up to {refill.spending_cap}; I'll file the HR claim as soon as you upload the receipt. "
            f"Please answer within {self.policy.nomination_timeout_hours:g} office hours or I'll ask the next person.",
            [Action("accept", "Yes, I'll do it", refill.id), Action("decline", "Not this time", refill.id)],
        )
        return True

    def _pass(self, refill: Refill, who: str, why: str, detail: str = "") -> None:
        """Take the refill away from ``who`` and put it back in the pool."""
        refill.passed.append(who)
        refill.state = S.REPORTED
        refill.nominee_id = None
        refill.nomination_expires_at = None
        refill.buyer_id = None
        refill.committed_at = None
        refill.purchase_deadline = None
        refill.reminder_sent = False
        refill.record(self.clock.now(), why, who, detail)
        self.repo.save_refill(refill)

    def _file_claim(self, refill: Refill) -> bool:
        office, counter = self._office_and_counter(refill)
        receipt, buyer = refill.receipt, refill.buyer_id
        assert receipt is not None and buyer is not None
        now = self.clock.now()
        try:
            claim_id = self.hr.create_claim(
                employee_id=buyer,
                amount=receipt.total.amount,
                currency=receipt.total.currency,
                description=f"Snack refill {refill.id}: {counter.name}, {office.name}, bought {receipt.purchased_on:%Y-%m-%d}",
                receipt_image=receipt.image,
                reference=refill.id,
            )
        except HRUnavailable as e:
            refill.claim_attempts += 1
            refill.record(now, "claim_failed", None, str(e))
            self.repo.save_refill(refill)
            if refill.claim_attempts == self.policy.max_claim_attempts:
                for admin in office.admins:
                    self.chat.dm(admin, f"🚨 I've failed {refill.claim_attempts} times to file the HR claim for "
                                        f"{refill.id} ({self._name(buyer)}, {receipt.total}). Is HR down?")
            return False
        except HRRejectedRequest as e:
            refill.state = S.REJECTED
            refill.closed_at = now
            refill.record(now, "claim_refused_by_hr", None, str(e))
            self.repo.save_refill(refill)
            self.chat.dm(buyer, f"❌ HR refused the claim for {refill.id}: {e}. An admin will sort this out with you.")
            for admin in office.admins:
                self.chat.dm(admin, f"❌ HR refused the claim for {refill.id} ({self._name(buyer)}, {receipt.total}): {e}")
            return False

        refill.claim_id = claim_id
        refill.claimed_at = now
        refill.state = S.CLAIMED
        refill.record(now, "claim_filed", None, claim_id)
        self.repo.save_refill(refill)
        self.chat.dm(buyer, f"🧾 Claim {claim_id} filed with HR for {receipt.total}. HR takes 1–10 days; "
                            f"I'll tell you when it's approved and nudge them if it drags.")
        self.chat.post(office.channel, f"🧾 The {counter.name} was refilled by {self._name(buyer)} ({receipt.total}). Claim filed.")
        return True

    def _follow_up_claim(self, refill: Refill, office: Office, counter_name: str, now: datetime) -> list[str]:
        assert refill.claim_id and refill.claimed_at and refill.buyer_id
        status = self.hr.claim_status(refill.claim_id)
        buyer = refill.buyer_id
        if status.value == "approved":
            refill.state = S.REIMBURSED
            refill.closed_at = now
            refill.record(now, "reimbursed", None, refill.claim_id)
            self.repo.save_refill(refill)
            payroll = self.repo.people[buyer].payroll_currency if buyer in self.repo.people else "your payroll currency"
            self.chat.dm(buyer, f"💸 HR approved claim {refill.claim_id}. The money comes with your next payroll in {payroll}. Thank you!")
            return [f"{refill.id}: reimbursed"]
        if status.value == "rejected":
            refill.state = S.REJECTED
            refill.closed_at = now
            refill.record(now, "claim_rejected", None, refill.claim_id)
            self.repo.save_refill(refill)
            self.chat.dm(buyer, f"❌ HR rejected claim {refill.claim_id} for the {counter_name} refill. An admin will follow up with you.")
            for admin in office.admins:
                self.chat.dm(admin, f"❌ HR rejected claim {refill.claim_id} ({self._name(buyer)}, {refill.id}). Please follow up.")
            return [f"{refill.id}: claim rejected"]
        waited = now - refill.claimed_at
        if not refill.hr_chased and waited >= timedelta(days=self.policy.hr_chase_after_days):
            refill.hr_chased = True
            refill.record(now, "hr_chased")
            self.repo.save_refill(refill)
            for admin in office.admins:
                self.chat.dm(admin, f"⏳ Claim {refill.claim_id} ({self._name(buyer)}, {refill.receipt.total if refill.receipt else ''}) "
                                    f"has been pending in HR for {waited.days} days. Could you chase it?")
            return [f"{refill.id}: chased HR after {waited.days} days"]
        return []

    def _post_report_if_month_rolled(self, now: datetime) -> list[str]:
        current = self._month(now)
        previous, self._last_report_month = self._last_report_month, current
        if current == previous or self.reporter is None:
            return []
        report = self.reporter.monthly(*previous)
        self.chat.post(self.policy.report_channel, report.render(self.repo.name_of))
        return [f"posted the {MONTHS[previous[1] - 1]} report to {self.policy.report_channel}"]

    def _alert_once(self, refill: Refill, office: Office, key: str, text: str) -> None:
        if key in refill.alerted:
            return
        refill.alerted.add(key)
        self.repo.save_refill(refill)
        self.chat.post(office.channel, text)
        for admin in office.admins:
            self.chat.dm(admin, text)

    def _already_in_progress(self, refill: Refill) -> str:
        counter = self.repo.counters[refill.counter_id].name
        if refill.state == S.COMMITTED:
            office = self.repo.offices[refill.office_id]
            assert refill.purchase_deadline
            return (f"Thanks — {self._name(refill.buyer_id or '')} is already buying for the {counter} "
                    f"(due {office.hours.fmt(refill.purchase_deadline)}).")
        if refill.state == S.NOMINATED:
            return f"Thanks — already on it: I've asked {self._name(refill.nominee_id or '')} to refill the {counter}."
        return f"Thanks — already on it: looking for someone to refill the {counter}."

    @staticmethod
    def _must_be_claimable(refill: Refill) -> None:
        if refill.state == S.COMMITTED:
            raise AlreadyCommitted(refill.buyer_id)
        if refill.state not in (S.REPORTED, S.NOMINATED):
            raise InvalidState(f"{refill.id} is {refill.state.value}; nobody can take it any more")

    def _must_be_admin(self, office: Office, user_id: str) -> None:
        if user_id not in office.admins:
            raise NotAllowed(f"{self._name(user_id)} is not an admin for {office.name}")

    def _office_and_counter(self, refill: Refill):
        return self.repo.offices[refill.office_id], self.repo.counters[refill.counter_id]

    def _name(self, user_id: str) -> str:
        return self.repo.name_of(user_id)

    @staticmethod
    def _month(dt: datetime) -> tuple[int, int]:
        return dt.year, dt.month

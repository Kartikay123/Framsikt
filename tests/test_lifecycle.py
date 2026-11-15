"""The refill lifecycle from 'empty' to 'reimbursed', and the ways it can go wrong."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from snackbot import (
    ClaimStatus, InvalidReceipt, InvalidState, Money, NotAllowed, ReceiptLine, RefillState as S,
)
from tests.world import make_world


class HappyPathTests(unittest.TestCase):
    def test_from_empty_to_reimbursed(self):
        w = make_world()

        # 1. Somebody reports the empty box. One person is asked, with a cap and a deadline.
        refill = w.service.report_empty("OSL-1", "dag", "both boxes empty")
        self.assertEqual(refill.state, S.NOMINATED)
        self.assertEqual(refill.nominee_id, "anna")                     # nobody has history: first by id
        ask = w.ask_for(refill)
        self.assertEqual([a.id for a in ask.actions], ["accept", "decline"])
        self.assertIn("1,200.00 NOK", ask.text)                        # nothing spent yet: cap = typical max
        self.assertIn("Tuesday", ask.text)                              # two office days: Mon + Tue
        channel = w.chat.in_channel("#snacks-oslo")
        self.assertEqual(len(channel), 1)
        self.assertIn("I'll buy it", [a.label for a in channel[0].actions])

        # 2. She accepts. The refill is locked to her.
        refill = w.accept(refill)
        self.assertEqual(refill.state, S.COMMITTED)
        self.assertEqual(refill.buyer_id, "anna")
        self.assertEqual(refill.purchase_deadline, datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc))  # Tue 18:00 CET
        self.assertIn("Anna Berg is buying", w.chat.in_channel("#snacks-oslo")[-1].text)

        # 3. She shops and uploads the receipt. The claim goes to HR immediately.
        w.clock.advance(hours=5)
        lines = [ReceiptLine("nuts", 5, Decimal("90")), ReceiptLine("chocolate", 8, Decimal("50"))]
        refill = w.service.submit_receipt(refill.id, "anna", w.receipt("850", "NOK", date(2026, 11, 2), lines))
        self.assertEqual(refill.state, S.CLAIMED)
        claim = w.hr.claims[refill.claim_id]
        self.assertEqual((claim.employee_id, claim.amount, claim.currency), ("anna", Decimal("850.00"), "NOK"))
        self.assertIn(refill.id, claim.description)
        self.assertIn("Oslo", claim.description)
        self.assertEqual(claim.receipt_image, "receipt.jpg")
        self.assertEqual(claim.status, ClaimStatus.PENDING)
        self.assertIn(refill.claim_id, w.chat.last_to("anna").text)
        self.assertEqual(w.budget.spent(w.oslo, 2026, 11), Money.of(850, "NOK"))

        # 4. HR approves four days later; the next scheduler run closes the loop.
        w.clock.advance(days=4)
        w.hr.approve(refill.claim_id)
        log = w.service.tick()
        refill = w.refill(refill.id)
        self.assertEqual(refill.state, S.REIMBURSED)
        self.assertIn(f"{refill.id}: reimbursed", log)
        self.assertIn("approved", w.chat.last_to("anna").text.lower())
        self.assertIn("NOK", w.chat.last_to("anna").text)
        self.assertEqual(
            [e.type for e in refill.history],
            ["reported", "nominated", "committed", "receipt_accepted", "claim_filed", "reimbursed"],
        )

    def test_reporting_the_same_empty_counter_twice_does_not_open_a_second_refill(self):
        w = make_world()
        first = w.service.report_empty("OSL-1", "dag")
        second = w.service.report_empty("OSL-1", "cecilie")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(w.repo.refills()), 1)
        self.assertIn("already on it", w.chat.last_to("cecilie").text)
        self.assertEqual(len(w.chat.to_user("anna")), 1, "the nominee is asked exactly once")

    def test_a_new_refill_can_start_once_the_previous_one_is_bought(self):
        w = make_world()
        first = w.accept(w.service.report_empty("OSL-1", "dag"))
        w.service.submit_receipt(first.id, "anna", w.receipt(600, "NOK"))
        second = w.service.report_empty("OSL-1", "dag")                 # box empty again a week later
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.nominee_id, "bjorn", "Anna just bought, so she is not first in line")


class ReceiptRuleTests(unittest.TestCase):
    def test_only_the_committed_buyer_can_submit_the_receipt(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        with self.assertRaises(NotAllowed):
            w.service.submit_receipt(refill.id, "bjorn", w.receipt(500, "NOK"))
        self.assertEqual(w.refill(refill.id).state, S.COMMITTED)
        self.assertEqual(w.repo.purchases(), [])

    def test_receipt_must_be_in_the_office_currency(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        with self.assertRaises(InvalidReceipt):
            w.service.submit_receipt(refill.id, "anna", w.receipt(4000, "INR"))
        self.assertEqual(w.refill(refill.id).state, S.COMMITTED, "nothing changed; she can try again")
        self.assertEqual(w.repo.purchases(), [])
        self.assertIn("NOK", w.chat.last_to("anna").text)

    def test_a_receipt_cannot_be_submitted_twice(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        w.service.submit_receipt(refill.id, "anna", w.receipt(500, "NOK"))
        with self.assertRaises(InvalidState):
            w.service.submit_receipt(refill.id, "anna", w.receipt(500, "NOK"))
        self.assertEqual(len(w.hr.claims), 1)


class HRIntegrationTests(unittest.TestCase):
    def test_hr_outage_is_retried_by_the_scheduler_without_duplicate_claims(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        w.hr.fail_next = 2
        refill = w.service.submit_receipt(refill.id, "anna", w.receipt(700, "NOK"))
        self.assertEqual(refill.state, S.PURCHASED)
        self.assertEqual((refill.claim_attempts, refill.claim_id), (1, None))
        self.assertEqual(w.budget.spent(w.oslo, 2026, 11), Money.of(700, "NOK"), "spend is recorded even while HR is down")

        w.service.tick()
        self.assertEqual(w.refill(refill.id).claim_attempts, 2)
        w.service.tick()
        refill = w.refill(refill.id)
        self.assertEqual(refill.state, S.CLAIMED)
        self.assertEqual(w.hr.calls, 3)
        self.assertEqual(len(w.hr.claims), 1)

    def test_filing_the_same_refill_twice_at_hr_yields_one_claim(self):
        w = make_world()
        kwargs = dict(employee_id="anna", amount=Decimal("500"), currency="NOK", description="x",
                      receipt_image="r.jpg", reference="R-0001")
        self.assertEqual(w.hr.create_claim(**kwargs), w.hr.create_claim(**kwargs))
        self.assertEqual(len(w.hr.claims), 1)

    def test_hr_rejection_closes_the_refill_and_tells_buyer_and_admin(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        refill = w.service.submit_receipt(refill.id, "anna", w.receipt(700, "NOK"))
        w.hr.reject(refill.claim_id, "receipt unreadable")
        w.service.tick()
        self.assertEqual(w.refill(refill.id).state, S.REJECTED)
        self.assertIn("rejected", w.chat.last_to("anna").text)
        self.assertIn(refill.claim_id, w.chat.last_to("adm-oslo").text)

    def test_slow_hr_is_chased_exactly_once_after_ten_days(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        refill = w.service.submit_receipt(refill.id, "anna", w.receipt(700, "NOK"))
        w.clock.advance(days=9)
        w.service.tick()
        self.assertFalse(any("pending in HR" in m.text for m in w.chat.to_user("adm-oslo")))
        w.clock.advance(days=1)
        w.service.tick()
        w.clock.advance(days=3)
        w.service.tick()
        chases = [m for m in w.chat.to_user("adm-oslo") if "pending in HR" in m.text]
        self.assertEqual(len(chases), 1)
        self.assertIn(refill.claim_id, chases[0].text)
        self.assertEqual(w.refill(refill.id).state, S.CLAIMED, "still waiting, still tracked")


class AdminTests(unittest.TestCase):
    def test_admin_can_cancel_a_false_alarm_and_the_counter_is_free_again(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")
        with self.assertRaises(NotAllowed):
            w.service.cancel(refill.id, "bjorn", "not an admin")
        w.service.cancel(refill.id, "adm-oslo", "box was refilled from the cupboard")
        self.assertEqual(w.refill(refill.id).state, S.CANCELLED)
        self.assertIn("cancelled", w.chat.last_to("anna").text)
        fresh = w.service.report_empty("OSL-1", "dag")
        self.assertNotEqual(fresh.id, refill.id)

    def test_a_bought_refill_cannot_be_cancelled(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        w.service.submit_receipt(refill.id, "anna", w.receipt(500, "NOK"))
        with self.assertRaises(InvalidState):
            w.service.cancel(refill.id, "adm-oslo", "too late")


class ReminderTests(unittest.TestCase):
    def test_buyer_is_reminded_once_before_the_deadline(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))   # deadline Tue 18:00 CET = 17:00 UTC
        w.clock.set(datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc))
        w.service.tick()
        self.assertFalse(any("Reminder" in m.text for m in w.chat.to_user("anna")))
        w.clock.set(datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc))
        w.service.tick()
        w.service.tick()
        reminders = [m for m in w.chat.to_user("anna") if "Reminder" in m.text]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(w.refill(refill.id).state, S.COMMITTED)


if __name__ == "__main__":
    unittest.main()

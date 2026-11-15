"""Budgets and totals are correct across both currencies."""
import unittest
from datetime import date, datetime, timezone

from snackbot import DateRange, InvalidReceipt, Money, RefillState as S, Visit
from tests.world import make_world


class SpendingCapTests(unittest.TestCase):
    def test_cap_is_the_smaller_of_what_is_left_and_a_typical_large_shop(self):
        w = make_world()
        on = date(2026, 11, 2)
        self.assertEqual(w.budget.spending_cap(w.oslo, on), Money.of(1200, "NOK"))
        w.seed_purchase("OSL", "anna", date(2026, 11, 1), 5500)
        self.assertEqual(w.budget.spending_cap(w.oslo, on), Money.of(500, "NOK"))
        self.assertFalse(w.budget.exhausted(w.oslo, on))
        w.seed_purchase("OSL", "bjorn", date(2026, 11, 1), 300)
        self.assertEqual(w.budget.spending_cap(w.oslo, on), Money.of(200, "NOK"))
        self.assertTrue(w.budget.exhausted(w.oslo, on), "200 NOK does not buy a 300 NOK minimum refill")
        self.assertEqual(w.budget.spending_cap(w.oslo, date(2026, 12, 1)), Money.of(1200, "NOK"), "next month is fresh")

    def test_the_nominee_is_told_the_cap(self):
        w = make_world()
        w.seed_purchase("OSL", "cecilie", date(2026, 11, 1), 5500)
        refill = w.service.report_empty("OSL-1", "dag")
        self.assertEqual(w.refill(refill.id).spending_cap, Money.of(500, "NOK"))
        self.assertIn("500.00 NOK", w.ask_for(refill).text)


class ReceiptCheckTests(unittest.TestCase):
    def setUp(self):
        self.w = make_world()
        self.w.seed_purchase("OSL", "cecilie", date(2026, 11, 1), 5500)      # 500 NOK left -> cap 500
        self.refill = self.w.accept(self.w.service.report_empty("OSL-1", "dag"))  # Anna, cap 500

    def test_a_receipt_within_ten_percent_of_the_cap_is_filed_automatically(self):
        refill = self.w.service.submit_receipt(self.refill.id, "anna", self.w.receipt(540, "NOK"))
        self.assertEqual(refill.state, S.CLAIMED)
        self.assertEqual(self.w.budget.spent(self.w.oslo, 2026, 11), Money.of(6040, "NOK"))

    def test_a_receipt_well_over_the_cap_waits_for_an_admin_but_counts_as_spend_at_once(self):
        refill = self.w.service.submit_receipt(self.refill.id, "anna", self.w.receipt(650, "NOK"))
        self.assertEqual(refill.state, S.NEEDS_APPROVAL)
        self.assertEqual(self.w.hr.claims, {}, "no claim until an admin says yes")
        self.assertEqual(self.w.budget.spent(self.w.oslo, 2026, 11), Money.of(6150, "NOK"), "the money is spent either way")
        ask = self.w.chat.last_to("adm-oslo")
        self.assertEqual([a.id for a in ask.actions], ["approve_overrun", "reject_overrun"])
        self.assertIn("-150.00 NOK left", ask.text)

        self.assertEqual(self.w.chat.click(ask.id, "approve_overrun", "adm-oslo"), "approved")
        refill = self.w.refill(refill.id)
        self.assertEqual(refill.state, S.CLAIMED)
        self.assertEqual(self.w.hr.claims[refill.claim_id].amount, Money.of(650, "NOK").amount)

    def test_only_an_office_admin_can_approve(self):
        refill = self.w.service.submit_receipt(self.refill.id, "anna", self.w.receipt(650, "NOK"))
        ask = self.w.chat.last_to("adm-oslo")
        self.assertTrue(self.w.chat.click(ask.id, "approve_overrun", "bjorn").startswith("rejected:"))
        self.assertIn("not an admin", self.w.chat.last_to("bjorn").text)
        self.assertEqual(self.w.refill(refill.id).state, S.NEEDS_APPROVAL)

    def test_a_refused_overrun_is_taken_out_of_the_spend(self):
        refill = self.w.service.submit_receipt(self.refill.id, "anna", self.w.receipt(650, "NOK"))
        self.w.service.reject_overrun(refill.id, "adm-oslo", "agreed cap was 500")
        self.assertEqual(self.w.refill(refill.id).state, S.REJECTED)
        self.assertEqual(self.w.budget.spent(self.w.oslo, 2026, 11), Money.of(5500, "NOK"))
        self.assertIn("did not approve", self.w.chat.last_to("anna").text)


class ExhaustedBudgetTests(unittest.TestCase):
    def test_an_exhausted_budget_blocks_nominations_until_the_month_rolls_over(self):
        w = make_world()
        w.seed_purchase("OSL", "cecilie", date(2026, 11, 1), 5800)           # 200 left < 300 minimum
        refill = w.service.report_empty("OSL-1", "dag")
        self.assertEqual((refill.state, refill.nominee_id), (S.REPORTED, None))
        alerts = lambda: [m for m in w.chat.in_channel("#snacks-oslo") if "used up" in m.text]
        self.assertEqual(len(alerts()), 1)
        self.assertIn("5,800.00 NOK of 6,000.00 NOK", alerts()[0].text)
        self.assertIn("used up", w.chat.last_to("adm-oslo").text)

        w.clock.advance(days=3)
        w.service.tick()
        self.assertEqual(w.refill(refill.id).nominee_id, None)
        self.assertEqual(len(alerts()), 1, "alerted once, not every five minutes")

        w.clock.set(datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
        w.service.tick()
        refill = w.refill(refill.id)
        self.assertEqual((refill.state, refill.nominee_id, refill.spending_cap), (S.NOMINATED, "anna", Money.of(1200, "NOK")))


class CurrencyTests(unittest.TestCase):
    def test_the_budget_month_is_decided_by_the_date_on_the_receipt(self):
        w = make_world()
        w.seed_purchase("OSL", "anna", date(2026, 11, 30), 400)
        w.seed_purchase("OSL", "bjorn", date(2026, 12, 1), 700)
        self.assertEqual(w.budget.spent(w.oslo, 2026, 11), Money.of(400, "NOK"))
        self.assertEqual(w.budget.spent(w.oslo, 2026, 12), Money.of(700, "NOK"))

    def test_each_office_budget_is_in_its_own_currency(self):
        w = make_world()
        self.assertEqual(w.oslo.budget, Money.of(6000, "NOK"))
        self.assertEqual(w.blr.budget, Money.of(15000, "INR"))
        w.seed_purchase("OSL", "anna", date(2026, 11, 3), 800)
        w.seed_purchase("BLR", "priya", date(2026, 11, 3), 2400)
        self.assertEqual(w.budget.remaining(w.oslo, 2026, 11), Money.of(5200, "NOK"))
        self.assertEqual(w.budget.remaining(w.blr, 2026, 11), Money.of(12600, "INR"))

    def test_a_visitor_buys_in_local_currency_against_the_local_budget_and_is_paid_in_payroll_currency(self):
        w = make_world()
        w.repo.people["anna"].visits.append(Visit("BLR", DateRange(date(2026, 11, 2), date(2026, 11, 13))))
        refill = w.service.report_empty("BLR-1", "rahul")
        self.assertEqual(refill.nominee_id, "anna", "the Oslo visitor is a candidate in Bengaluru")
        self.assertEqual(refill.spending_cap, Money.of(3000, "INR"))
        refill = w.accept(refill)

        with self.assertRaises(InvalidReceipt):
            w.service.submit_receipt(refill.id, "anna", w.receipt(250, "NOK"))   # paid in rupees, claim in rupees

        refill = w.service.submit_receipt(refill.id, "anna", w.receipt(2000, "INR", date(2026, 11, 2)))
        self.assertEqual(refill.state, S.CLAIMED)
        claim = w.hr.claims[refill.claim_id]
        self.assertEqual((claim.employee_id, claim.currency, claim.amount), ("anna", "INR", Money.of(2000, "INR").amount))
        self.assertEqual(w.budget.spent(w.blr, 2026, 11), Money.of(2000, "INR"))
        self.assertEqual(w.budget.spent(w.oslo, 2026, 11), Money.of(0, "NOK"))

        w.hr.approve(refill.claim_id)
        self.assertEqual(w.hr.claims[refill.claim_id].payout, Money.of(250, "NOK"), "HR pays her in her payroll currency")
        w.service.tick()
        self.assertEqual(w.refill(refill.id).state, S.REIMBURSED)


if __name__ == "__main__":
    unittest.main()

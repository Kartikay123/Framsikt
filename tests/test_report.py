"""One monthly report across both offices."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from snackbot import Money, ReceiptLine, RefillState as S
from tests.world import make_world


class MonthlyReportTests(unittest.TestCase):
    def test_totals_both_offices_in_one_currency(self):
        w = make_world()
        nuts = ReceiptLine("nuts", 4, Decimal("200"))
        w.seed_purchase("OSL", "anna", date(2026, 11, 3), 800, [nuts])
        w.seed_purchase("OSL", "bjorn", date(2026, 11, 10), "650.50")
        w.seed_purchase("BLR", "priya", date(2026, 11, 4), 2400, [ReceiptLine("murukku", 12, Decimal("200"))])
        w.seed_purchase("BLR", "rahul", date(2026, 11, 12), 1600, [ReceiptLine("murukku", 4, Decimal("100")),
                                                                    ReceiptLine("fruit", 8, Decimal("150"))])
        w.seed_purchase("OSL", "anna", date(2026, 10, 28), 999)   # last month: must not appear

        report = w.reporter.monthly(2026, 11)
        oslo, blr = report.offices
        self.assertEqual((oslo.refills, oslo.spent, oslo.utilisation_pct), (2, Money.of("1450.50", "NOK"), Decimal("24.2")))
        self.assertEqual((blr.refills, blr.spent, blr.utilisation_pct), (2, Money.of(4000, "INR"), Decimal("26.7")))
        self.assertEqual(report.total, Money.of("1950.50", "NOK"))   # 1,450.50 + 4,000 × 0.125
        self.assertEqual(blr.top_items, [("murukku", 16), ("fruit", 8)])
        self.assertEqual(oslo.by_buyer, [("anna", 1), ("bjorn", 1)])

        text = report.render(w.repo.name_of)
        self.assertIn("Snack spend — November 2026", text)
        self.assertIn("Oslo: 2 refills, 1,450.50 NOK of 6,000.00 NOK (24.2%)", text)
        self.assertIn("Bengaluru: 2 refills, 4,000.00 INR of 15,000.00 INR (26.7%)", text)
        self.assertIn("Total: 1,950.50 NOK (1 INR = 0.1250 NOK, rates as of 2026-11-01)", text)
        self.assertNotIn("OVER BUDGET", text)

    def test_overspend_is_flagged(self):
        w = make_world()
        w.seed_purchase("OSL", "anna", date(2026, 11, 3), 6100)
        report = w.reporter.monthly(2026, 11)
        self.assertTrue(report.offices[0].over_budget)
        self.assertIn("OVER BUDGET", report.render())

    def test_report_distinguishes_paid_awaiting_and_refused(self):
        w = make_world()
        first = w.accept(w.service.report_empty("OSL-1", "dag"))                       # Anna
        w.service.submit_receipt(first.id, "anna", w.receipt(900, "NOK", date(2026, 11, 2)))  # CLAIMED, awaiting HR
        second = w.accept(w.service.report_empty("OSL-1", "dag"))                      # Bjørn, cap 1200
        w.service.submit_receipt(second.id, "bjorn", w.receipt(2000, "NOK", date(2026, 11, 3)))
        w.service.reject_overrun(second.id, "adm-oslo", "far over the cap")

        oslo = w.reporter.monthly(2026, 11).offices[0]
        self.assertEqual((oslo.refills, oslo.spent, oslo.awaiting_hr, oslo.refused), (1, Money.of(900, "NOK"), 1, 1))

    def test_the_report_is_posted_to_management_when_the_month_rolls_over(self):
        w = make_world()
        w.seed_purchase("BLR", "priya", date(2026, 11, 4), 2400)
        w.clock.set(datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc))
        w.service.tick()
        self.assertEqual(w.chat.in_channel("#management"), [])
        w.clock.set(datetime(2026, 12, 1, 0, 5, tzinfo=timezone.utc))
        log = w.service.tick()
        posts = w.chat.in_channel("#management")
        self.assertEqual(len(posts), 1)
        self.assertIn("November 2026", posts[0].text)
        self.assertIn("Total: 300.00 NOK", posts[0].text)
        self.assertIn("posted the November report to #management", log)
        w.service.tick()
        self.assertEqual(len(w.chat.in_channel("#management")), 1)


if __name__ == "__main__":
    unittest.main()

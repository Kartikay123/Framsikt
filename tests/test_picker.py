"""Choosing who buys: able to get there, and the one who has done the least lately."""
import unittest
from datetime import date

from snackbot import Counter, DateRange, Picker, PickerPolicy, Refill, RefillState as S, Visit
from tests.world import START, make_world

THIS_WEEK = DateRange(date(2026, 11, 2), date(2026, 11, 13))


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.w = make_world()
        self.picker = Picker(self.w.repo)
        self.days = self.w.oslo.hours.purchase_days(self.w.clock.now(), 2)  # Mon 2 Nov, Tue 3 Nov

    def ids(self, office=None, days=None, exclude=()):
        office = office or self.w.oslo
        return [r.person.id for r in self.picker.ranked(office, days or self.days, exclude)]

    def test_prefers_the_person_who_has_not_bought_for_longest(self):
        self.w.seed_purchase("OSL", "anna", date(2026, 10, 20), 600)
        self.w.seed_purchase("OSL", "bjorn", date(2026, 9, 1), 600)
        self.w.seed_purchase("OSL", "dag", date(2026, 10, 30), 600)
        self.assertEqual(self.ids(), ["cecilie", "bjorn", "anna", "dag"])

    def test_with_no_history_the_order_is_deterministic(self):
        self.assertEqual(self.ids(), ["anna", "bjorn", "cecilie", "dag"])
        self.assertEqual(self.ids(), self.ids())

    def test_skips_people_who_are_not_at_the_office_in_the_purchase_window(self):
        self.w.repo.people["cecilie"].absences.append(DateRange(date(2026, 11, 2), date(2026, 11, 6)))  # holiday
        self.w.repo.people["dag"].office_days = frozenset({3, 4})                                       # Thu/Fri only
        self.assertEqual(self.ids(), ["anna", "bjorn"])
        thursday_friday = [date(2026, 11, 5), date(2026, 11, 6)]
        self.assertIn("dag", self.ids(days=thursday_friday))

    def test_visitors_count_at_the_office_they_are_actually_in(self):
        self.w.repo.people["priya"].visits.append(Visit("OSL", THIS_WEEK))   # Priya is in Oslo
        self.w.repo.people["anna"].visits.append(Visit("BLR", THIS_WEEK))    # Anna is in Bengaluru
        oslo = self.ids()
        blr = self.ids(office=self.w.blr, days=self.w.blr.hours.purchase_days(self.w.clock.now(), 2))
        self.assertIn("priya", oslo)
        self.assertNotIn("anna", oslo)
        self.assertIn("anna", blr)
        self.assertNotIn("priya", blr)

    def test_skips_people_already_waiting_on_two_reimbursements(self):
        for n in (1, 2):
            self.w.repo.insert_refill(Refill(f"old-{n}", "OSL-1", "OSL", "x", START,
                                             state=S.CLAIMED, buyer_id="anna"))
        self.assertEqual(self.ids(), ["bjorn", "cecilie", "dag"])
        lenient = Picker(self.w.repo, PickerPolicy(max_outstanding_claims=3))
        self.assertIn("anna", [r.person.id for r in lenient.ranked(self.w.oslo, self.days)])

    def test_skips_people_busy_with_another_open_refill(self):
        self.w.repo.add_counter(Counter("OSL-2", "OSL", "Oslo fruit bowl"))
        other = self.w.service.report_empty("OSL-2", "dag")
        self.assertEqual(other.nominee_id, "anna")
        self.assertEqual(self.ids(), ["bjorn", "cecilie", "dag"])

    def test_excludes_people_who_already_passed_on_this_refill(self):
        self.assertEqual(self.ids(exclude=["anna", "cecilie"]), ["bjorn", "dag"])

    def test_fewer_recent_purchases_wins_when_last_purchase_dates_tie(self):
        self.w.seed_purchase("OSL", "anna", date(2026, 9, 10), 500)
        self.w.seed_purchase("OSL", "anna", date(2026, 10, 1), 500)
        self.w.seed_purchase("OSL", "bjorn", date(2026, 10, 1), 500)
        self.w.seed_purchase("OSL", "cecilie", date(2026, 10, 1), 500)
        self.w.seed_purchase("OSL", "cecilie", date(2026, 7, 1), 500)      # outside the 90-day window
        self.w.seed_purchase("OSL", "dag", date(2026, 10, 1), 500)
        self.assertEqual(self.ids(), ["bjorn", "cecilie", "dag", "anna"])

    def test_nobody_available_returns_none_and_the_service_asks_the_room(self):
        for pid in ["anna", "bjorn", "cecilie", "dag"]:
            self.w.repo.people[pid].absences.append(DateRange(date(2026, 11, 1), date(2026, 11, 30)))
        self.assertIsNone(self.picker.pick(self.w.oslo, self.days))
        refill = self.w.service.report_empty("OSL-1", "adm-oslo")
        self.assertEqual((refill.state, refill.nominee_id), (S.REPORTED, None))
        self.assertIn("Nobody", self.w.chat.in_channel("#snacks-oslo")[-1].text)


if __name__ == "__main__":
    unittest.main()

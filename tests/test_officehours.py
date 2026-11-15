import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from snackbot import OfficeHours

CET = ZoneInfo("Europe/Oslo")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CET)


class OfficeHoursTests(unittest.TestCase):
    oslo = OfficeHours("Europe/Oslo")

    def test_two_office_hours_on_a_monday_morning_is_just_two_hours(self):
        self.assertEqual(self.oslo.add_office_hours(at(2026, 11, 2, 9), 2), at(2026, 11, 2, 11))

    def test_a_friday_evening_timeout_runs_into_monday_morning(self):
        # Friday 17:00 + 2 office hours: one hour left on Friday, one hour on Monday.
        self.assertEqual(self.oslo.add_office_hours(at(2026, 11, 6, 17), 2), at(2026, 11, 9, 9))

    def test_weekend_starts_counting_at_monday_opening(self):
        self.assertEqual(self.oslo.add_office_hours(at(2026, 11, 7, 12), 2), at(2026, 11, 9, 10))

    def test_before_opening_starts_counting_at_opening(self):
        self.assertEqual(self.oslo.add_office_hours(at(2026, 11, 2, 6, 30), 1), at(2026, 11, 2, 9))

    def test_purchase_days_include_today_only_while_the_office_is_open(self):
        self.assertEqual(self.oslo.purchase_days(at(2026, 11, 6, 10), 2), [date(2026, 11, 6), date(2026, 11, 9)])
        self.assertEqual(self.oslo.purchase_days(at(2026, 11, 6, 19), 2), [date(2026, 11, 9), date(2026, 11, 10)])

    def test_time_zones_are_respected(self):
        blr = OfficeHours("Asia/Kolkata")
        # 13:30 UTC is 19:00 in Bengaluru: closed, so the next open moment is tomorrow 08:00 IST.
        utc = datetime(2026, 11, 2, 13, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(blr.next_open(utc), datetime(2026, 11, 3, 8, tzinfo=ZoneInfo("Asia/Kolkata")))


if __name__ == "__main__":
    unittest.main()

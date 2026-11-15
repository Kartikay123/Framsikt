"""Office-hour arithmetic.

Volunteer timeouts are counted in *office* hours, not wall-clock hours: a nomination
sent on Friday at 17:30 must not expire on Friday night while everyone is at home.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Monday..Friday


@dataclass(frozen=True)
class OfficeHours:
    tz: str
    open_at: time = time(8, 0)
    close_at: time = time(18, 0)
    workdays: frozenset[int] = WEEKDAYS

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.zone)

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.workdays

    def opening(self, d: date) -> datetime:
        return datetime.combine(d, self.open_at, tzinfo=self.zone)

    def closing(self, d: date) -> datetime:
        return datetime.combine(d, self.close_at, tzinfo=self.zone)

    def next_workday(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_workday(d):
            d += timedelta(days=1)
        return d

    def next_open(self, dt: datetime) -> datetime:
        """The earliest instant at or after ``dt`` that falls inside office hours."""
        local = self.local(dt)
        today = local.date()
        if self.is_workday(today):
            if local < self.opening(today):
                return self.opening(today)
            if local < self.closing(today):
                return local
        return self.opening(self.next_workday(today))

    def add_office_hours(self, dt: datetime, hours: float) -> datetime:
        """``dt`` plus ``hours`` of office time, skipping nights and weekends."""
        remaining = timedelta(hours=hours)
        cursor = self.next_open(dt)
        while True:
            close = self.closing(cursor.date())
            if cursor + remaining <= close:
                return cursor + remaining
            remaining -= close - cursor
            cursor = self.opening(self.next_workday(cursor.date()))

    def purchase_days(self, dt: datetime, n: int) -> list[date]:
        """The next ``n`` office days a buyer could shop on. Today counts if the office is still open."""
        days = [self.next_open(dt).date()]
        while len(days) < n:
            days.append(self.next_workday(days[-1]))
        return days

    def fmt(self, dt: datetime) -> str:
        return self.local(dt).strftime("%a %d %b %H:%M")

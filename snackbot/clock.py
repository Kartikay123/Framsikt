"""An injectable clock. The service never calls ``datetime.now()`` directly, so tests can
move time forward and prove what happens when nobody answers for two days."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Always timezone-aware, always UTC."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware start")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> datetime:
        self._now += delta if delta is not None else timedelta(**kwargs)
        return self._now

    def set(self, when: datetime) -> datetime:
        if when.tzinfo is None:
            raise ValueError("FakeClock.set needs a timezone-aware datetime")
        self._now = when.astimezone(timezone.utc)
        return self._now

"""Choosing who buys.

Two goals: the person we ask must actually be able to get to a shop and to the counter in
the next couple of days, and it should be the person who has done the least lately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from .model import Office, Person
from .store import Repository


@dataclass(frozen=True)
class PickerPolicy:
    lookback_days: int = 90
    max_outstanding_claims: int = 2  # never ask someone already waiting on this many reimbursements


@dataclass(frozen=True)
class Ranked:
    person: Person
    last_purchase_on: Optional[date]
    recent_purchases: int
    outstanding_claims: int

    def sort_key(self) -> tuple:
        # never bought → first; then longest ago; then fewest recently; then least money out; then id
        return (
            self.last_purchase_on or date.min,
            self.recent_purchases,
            self.outstanding_claims,
            self.person.id,
        )


class Picker:
    def __init__(self, repo: Repository, policy: PickerPolicy = PickerPolicy()) -> None:
        self.repo = repo
        self.policy = policy

    def ranked(self, office: Office, days: Sequence[date], exclude: Iterable[str] = ()) -> list[Ranked]:
        """Everyone who could do this refill, best candidate first."""
        excluded = set(exclude) | self.repo.busy_people()
        since = days[0] - timedelta(days=self.policy.lookback_days)
        candidates = []
        for person in self.repo.people.values():
            if not person.volunteer or person.id in excluded:
                continue
            if not any(person.location_on(d) == office.id for d in days):
                continue  # not at this office on any day the shopping could happen
            outstanding = self.repo.outstanding_claims(person.id)
            if outstanding >= self.policy.max_outstanding_claims:
                continue  # already floating enough of their own money
            candidates.append(Ranked(
                person,
                self.repo.last_purchase_on(person.id),
                self.repo.purchases_since(person.id, since),
                outstanding,
            ))
        candidates.sort(key=Ranked.sort_key)
        return candidates

    def pick(self, office: Office, days: Sequence[date], exclude: Iterable[str] = ()) -> Optional[Person]:
        ranked = self.ranked(office, days, exclude)
        return ranked[0].person if ranked else None

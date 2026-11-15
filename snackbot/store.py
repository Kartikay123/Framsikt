"""An in-memory repository standing in for the database.

Two rules the real database would enforce are enforced here, under a lock, because the
whole design leans on them:

1. at most one *open* refill per counter (a partial unique index in Postgres);
2. optimistic locking on refills: saving a stale copy fails
   (``UPDATE ... WHERE id = ? AND version = ?`` touching zero rows).

``get_refill`` hands out copies, so the caller's edits are invisible until ``save_refill``
succeeds, exactly like a row read in one transaction.
"""
from __future__ import annotations

import copy
import threading
from datetime import date
from typing import Callable, Optional

from .model import OPEN_STATES, OUT_OF_POCKET_STATES, Counter, Office, Person, Purchase, Refill


class ConflictError(Exception):
    """Someone else changed this refill since you read it."""


class DuplicateOpenRefill(Exception):
    def __init__(self, existing: Refill) -> None:
        super().__init__(f"{existing.counter_id} already has open refill {existing.id}")
        self.existing = existing


class Repository:
    def __init__(self) -> None:
        self.offices: dict[str, Office] = {}
        self.counters: dict[str, Counter] = {}
        self.people: dict[str, Person] = {}
        self._refills: dict[str, Refill] = {}
        self._purchases: list[Purchase] = []
        self._lock = threading.RLock()
        self._seq = 0

    # -- reference data ----------------------------------------------------------------------
    def add_office(self, office: Office) -> Office:
        self.offices[office.id] = office
        return office

    def add_counter(self, counter: Counter) -> Counter:
        self.counters[counter.id] = counter
        return counter

    def add_person(self, person: Person) -> Person:
        self.people[person.id] = person
        return person

    def name_of(self, user_id: str) -> str:
        person = self.people.get(user_id)
        return person.name if person else user_id

    # -- refills -----------------------------------------------------------------------------
    def new_refill_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"R-{self._seq:04d}"

    def insert_refill(self, refill: Refill) -> Refill:
        with self._lock:
            existing = self.open_refill_for(refill.counter_id)
            if existing is not None:
                raise DuplicateOpenRefill(existing)
            refill.version = 1
            self._refills[refill.id] = copy.deepcopy(refill)
            return refill

    def get_refill(self, refill_id: str) -> Refill:
        with self._lock:
            return copy.deepcopy(self._refills[refill_id])

    def save_refill(self, refill: Refill) -> Refill:
        with self._lock:
            current = self._refills[refill.id]
            if current.version != refill.version:
                raise ConflictError(
                    f"{refill.id} is at version {current.version}, you hold version {refill.version}"
                )
            refill.version += 1
            self._refills[refill.id] = copy.deepcopy(refill)
            return refill

    def open_refill_for(self, counter_id: str) -> Optional[Refill]:
        with self._lock:
            for r in self._refills.values():
                if r.counter_id == counter_id and r.state in OPEN_STATES:
                    return copy.deepcopy(r)
            return None

    def refills(self, where: Callable[[Refill], bool] = lambda r: True) -> list[Refill]:
        with self._lock:
            return [copy.deepcopy(r) for r in self._refills.values() if where(r)]

    def busy_people(self) -> set[str]:
        """Everyone currently nominated for, or committed to, some open refill."""
        with self._lock:
            busy = set()
            for r in self._refills.values():
                if r.state in OPEN_STATES:
                    busy.update(x for x in (r.nominee_id, r.buyer_id) if x)
            return busy

    # -- spend ledger ------------------------------------------------------------------------
    def add_purchase(self, purchase: Purchase) -> Purchase:
        with self._lock:
            self._purchases.append(purchase)
            return purchase

    def purchases(self, office_id: Optional[str] = None, year: Optional[int] = None,
                  month: Optional[int] = None) -> list[Purchase]:
        with self._lock:
            return [
                p for p in self._purchases
                if (office_id is None or p.office_id == office_id)
                and (year is None or p.purchased_on.year == year)
                and (month is None or p.purchased_on.month == month)
            ]

    def set_reimbursable(self, refill_id: str, reimbursable: bool) -> None:
        with self._lock:
            for p in self._purchases:
                if p.refill_id == refill_id:
                    p.reimbursable = reimbursable

    # -- history the picker needs ------------------------------------------------------------
    def last_purchase_on(self, person_id: str) -> Optional[date]:
        with self._lock:
            dates = [p.purchased_on for p in self._purchases if p.buyer_id == person_id]
            return max(dates) if dates else None

    def purchases_since(self, person_id: str, since: date) -> int:
        with self._lock:
            return sum(1 for p in self._purchases if p.buyer_id == person_id and p.purchased_on >= since)

    def outstanding_claims(self, person_id: str) -> int:
        """Refills this person has paid for and not yet been reimbursed for."""
        with self._lock:
            return sum(
                1 for r in self._refills.values()
                if r.buyer_id == person_id and r.state in OUT_OF_POCKET_STATES
            )

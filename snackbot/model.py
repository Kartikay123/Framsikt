"""The domain: offices, people, refills, receipts and the spend ledger."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from .money import Money
from .officehours import WEEKDAYS, OfficeHours


class RefillState(str, Enum):
    REPORTED = "reported"              # box is empty, nobody has been asked yet (or everyone passed)
    NOMINATED = "nominated"            # one volunteer has been asked and has a short time to answer
    COMMITTED = "committed"            # exactly one buyer is locked in, with a deadline
    PURCHASED = "purchased"            # receipt accepted, claim not yet filed with HR
    NEEDS_APPROVAL = "needs_approval"  # receipt is over the agreed cap: an admin must approve first
    CLAIMED = "claimed"                # claim is in the HR system, waiting for approval
    REIMBURSED = "reimbursed"          # HR approved; money comes with payroll. Terminal.
    REJECTED = "rejected"              # HR or management refused. Terminal, needs a human.
    CANCELLED = "cancelled"            # false alarm or box filled some other way. Terminal.


OPEN_STATES = frozenset({RefillState.REPORTED, RefillState.NOMINATED, RefillState.COMMITTED})
OUT_OF_POCKET_STATES = frozenset({RefillState.PURCHASED, RefillState.NEEDS_APPROVAL, RefillState.CLAIMED})
TERMINAL_STATES = frozenset({RefillState.REIMBURSED, RefillState.REJECTED, RefillState.CANCELLED})


class ClaimStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class Office:
    id: str
    name: str
    currency: str
    monthly_budget: Decimal
    typical_purchase: tuple[Decimal, Decimal]  # (smallest, largest) a single refill usually costs
    hours: OfficeHours
    channel: str                               # chat channel for this office's snack traffic
    admins: tuple[str, ...] = ()               # people who approve overruns and get escalations

    @property
    def budget(self) -> Money:
        return Money(self.monthly_budget, self.currency)

    @property
    def typical_min(self) -> Money:
        return Money(self.typical_purchase[0], self.currency)

    @property
    def typical_max(self) -> Money:
        return Money(self.typical_purchase[1], self.currency)


@dataclass
class Counter:
    id: str
    office_id: str
    name: str


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # inclusive

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and self.start <= d <= self.end


@dataclass(frozen=True)
class Visit:
    office_id: str
    days: DateRange


@dataclass
class Person:
    """An employee. Volunteers are people with ``volunteer=True``; admins usually are not."""

    id: str                      # the employee id the HR system knows
    name: str
    home_office: str
    payroll_currency: str
    volunteer: bool = True
    office_days: frozenset[int] = WEEKDAYS      # weekdays this person is normally at the office
    absences: list[DateRange] = field(default_factory=list)  # holiday, sick, working from home
    visits: list[Visit] = field(default_factory=list)        # weeks spent at the other office

    def location_on(self, d: date) -> Optional[str]:
        """Which office this person is physically at on ``d``; None means home, holiday or weekend."""
        if any(d in a for a in self.absences):
            return None
        for v in self.visits:
            if d in v.days:
                return v.office_id if d.weekday() in WEEKDAYS else None
        return self.home_office if d.weekday() in self.office_days else None


@dataclass(frozen=True)
class ReceiptLine:
    item: str
    quantity: int
    unit_price: Decimal

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Receipt:
    total: Money
    purchased_on: date   # the date printed on the receipt; decides which month's budget it hits
    image: str           # reference to the uploaded photo
    lines: tuple[ReceiptLine, ...] = ()

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("a receipt needs a photo")
        if not self.total.is_positive:
            raise ValueError("receipt total must be positive")
        if self.lines:
            lines_total = sum((line.total for line in self.lines), Decimal(0))
            if Money(lines_total, self.total.currency) != self.total:
                raise ValueError(f"line items add up to {lines_total}, receipt total is {self.total.amount}")


@dataclass
class Event:
    at: datetime
    type: str
    actor: Optional[str] = None
    detail: str = ""


@dataclass
class Refill:
    """One trip from 'the box is empty' to 'the buyer has their money back'."""

    id: str
    counter_id: str
    office_id: str
    reported_by: str
    reported_at: datetime
    note: str = ""
    state: RefillState = RefillState.REPORTED
    version: int = 0  # optimistic-locking version, bumped by the repository on every save

    # nomination
    nominee_id: Optional[str] = None
    nomination_expires_at: Optional[datetime] = None
    spending_cap: Optional[Money] = None

    # commitment
    buyer_id: Optional[str] = None
    committed_at: Optional[datetime] = None
    purchase_deadline: Optional[datetime] = None
    reminder_sent: bool = False

    # purchase and claim
    receipt: Optional[Receipt] = None
    purchased_at: Optional[datetime] = None
    claim_id: Optional[str] = None
    claimed_at: Optional[datetime] = None
    claim_attempts: int = 0
    hr_chased: bool = False
    closed_at: Optional[datetime] = None

    # bookkeeping
    passed: list[str] = field(default_factory=list)   # people who declined, timed out or handed back
    alerted: set[str] = field(default_factory=set)    # one-shot alerts already sent for this refill
    history: list[Event] = field(default_factory=list)

    def record(self, at: datetime, type: str, actor: Optional[str] = None, detail: str = "") -> None:
        self.history.append(Event(at, type, actor, detail))

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    @property
    def is_active(self) -> bool:
        return self.state not in TERMINAL_STATES


@dataclass
class Purchase:
    """One row in the spend ledger, written when a receipt is accepted."""

    refill_id: str
    office_id: str
    buyer_id: str
    purchased_on: date
    total: Money
    lines: tuple[ReceiptLine, ...] = ()
    reimbursable: bool = True  # set to False when management refuses an over-budget purchase

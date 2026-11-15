"""Simulated stand-ins for the two external systems: the company chat tool and the HR system.

From the service's point of view they behave like the real things. For tests and the demo
they also expose what the real things would let humans do: press a button, approve a claim,
be down for a while.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional, Sequence

from .clock import Clock
from .model import ClaimStatus
from .money import FxRates, Money
from .ports import Action, HRRejectedRequest, HRUnavailable


@dataclass(frozen=True)
class Message:
    id: str
    kind: str          # "channel" or "dm"
    to: str            # channel name or user id
    text: str
    actions: tuple[Action, ...]
    at: datetime


class FakeChat:
    """Records every message the service sends and lets a test 'press' the buttons."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.messages: list[Message] = []
        self.on_action: Optional[Callable[[str, Action], object]] = None

    # -- ChatPort -------------------------------------------------------------------------
    def post(self, channel: str, text: str, actions: Sequence[Action] = ()) -> str:
        return self._send("channel", channel, text, actions)

    def dm(self, user_id: str, text: str, actions: Sequence[Action] = ()) -> str:
        return self._send("dm", user_id, text, actions)

    def _send(self, kind: str, to: str, text: str, actions: Sequence[Action]) -> str:
        with self._lock:
            msg = Message(f"m{len(self.messages) + 1}", kind, to, text, tuple(actions), self._clock.now())
            self.messages.append(msg)
            return msg.id

    # -- what a user does in the chat client -----------------------------------------------
    def click(self, message_id: str, action_id: str, as_user: str) -> object:
        """Simulate ``as_user`` pressing the button ``action_id`` under ``message_id``."""
        msg = next(m for m in self.messages if m.id == message_id)
        action = next(a for a in msg.actions if a.id == action_id)
        if self.on_action is None:
            raise RuntimeError("nothing is listening for button clicks")
        return self.on_action(as_user, action)

    # -- helpers for assertions ------------------------------------------------------------
    def to_user(self, user_id: str) -> list[Message]:
        return [m for m in self.messages if m.kind == "dm" and m.to == user_id]

    def in_channel(self, channel: str) -> list[Message]:
        return [m for m in self.messages if m.kind == "channel" and m.to == channel]

    def last_to(self, user_id: str) -> Optional[Message]:
        msgs = self.to_user(user_id)
        return msgs[-1] if msgs else None

    def texts(self, *, to: Optional[str] = None) -> list[str]:
        return [m.text for m in self.messages if to is None or m.to == to]

    def transcript(self) -> str:
        lines = []
        for m in self.messages:
            where = m.to if m.kind == "channel" else f"DM → {m.to}"
            buttons = f"   [{' | '.join(a.label for a in m.actions)}]" if m.actions else ""
            lines.append(f"{m.at:%Y-%m-%d %H:%M}Z  {where:<16} {m.text}{buttons}")
        return "\n".join(lines)


@dataclass
class HRClaim:
    id: str
    employee_id: str
    amount: Decimal
    currency: str
    description: str
    receipt_image: str
    reference: str
    status: ClaimStatus
    created_at: datetime
    decided_at: Optional[datetime] = None
    payout: Optional[Money] = None   # what the employee actually receives, in payroll currency
    reason: str = ""


class FakeHR:
    """The external HR system: accepts claims, HR staff approve or reject them, and the
    employee is paid in the currency of their payroll."""

    ACCEPTED_CURRENCIES = frozenset({"NOK", "INR"})

    def __init__(self, clock: Clock, employees: dict[str, str], fx: Optional[FxRates] = None) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.employees = dict(employees)       # employee id -> payroll currency
        self.fx = fx
        self.claims: dict[str, HRClaim] = {}
        self._by_reference: dict[str, str] = {}
        self.fail_next = 0                     # make the next N calls fail, like an outage
        self.calls = 0

    # -- HRPort ---------------------------------------------------------------------------
    def create_claim(
        self,
        *,
        employee_id: str,
        amount: Decimal,
        currency: str,
        description: str,
        receipt_image: str,
        reference: str,
    ) -> str:
        with self._lock:
            self.calls += 1
            if self.fail_next > 0:
                self.fail_next -= 1
                raise HRUnavailable("HR system timed out")
            if employee_id not in self.employees:
                raise HRRejectedRequest(f"unknown employee {employee_id}")
            if amount <= 0:
                raise HRRejectedRequest("amount must be positive")
            if currency not in self.ACCEPTED_CURRENCIES:
                raise HRRejectedRequest(f"currency {currency} not accepted")
            if not receipt_image:
                raise HRRejectedRequest("receipt image is required")
            if reference in self._by_reference:          # idempotent on our reference
                return self._by_reference[reference]
            claim_id = f"HR-{1000 + len(self.claims) + 1}"
            self.claims[claim_id] = HRClaim(
                claim_id, employee_id, amount, currency, description, receipt_image, reference,
                ClaimStatus.PENDING, self._clock.now(),
            )
            self._by_reference[reference] = claim_id
            return claim_id

    def claim_status(self, claim_id: str) -> ClaimStatus:
        return self.claims[claim_id].status

    # -- what HR staff do -----------------------------------------------------------------
    def approve(self, claim_id: str) -> HRClaim:
        claim = self.claims[claim_id]
        claimed = Money(claim.amount, claim.currency)
        payroll_currency = self.employees[claim.employee_id]
        if claimed.currency == payroll_currency:
            claim.payout = claimed
        elif self.fx is not None:
            claim.payout = self.fx.convert(claimed, payroll_currency)
        else:
            raise RuntimeError("FakeHR needs FX rates to pay out across currencies")
        claim.status = ClaimStatus.APPROVED
        claim.decided_at = self._clock.now()
        return claim

    def reject(self, claim_id: str, reason: str) -> HRClaim:
        claim = self.claims[claim_id]
        claim.status = ClaimStatus.REJECTED
        claim.reason = reason
        claim.decided_at = self._clock.now()
        return claim

    def pending(self) -> list[HRClaim]:
        return [c for c in self.claims.values() if c.status == ClaimStatus.PENDING]

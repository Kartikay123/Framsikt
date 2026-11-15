"""The two external systems, as the service sees them."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from .model import ClaimStatus


@dataclass(frozen=True)
class Action:
    """A button under a chat message. Pressing it calls back into the service."""

    id: str
    label: str
    refill_id: str


class ChatPort(Protocol):
    def post(self, channel: str, text: str, actions: Sequence[Action] = ()) -> str: ...

    def dm(self, user_id: str, text: str, actions: Sequence[Action] = ()) -> str: ...


class HRUnavailable(Exception):
    """The HR system did not take the request (timeout, 5xx). Safe to retry later."""


class HRRejectedRequest(ValueError):
    """The HR system refused the claim as malformed. Retrying blindly will not help."""


class HRPort(Protocol):
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
        """Create a reimbursement claim and return its id. ``reference`` is our refill id;
        sending the same reference twice must not create two claims."""
        ...

    def claim_status(self, claim_id: str) -> ClaimStatus: ...

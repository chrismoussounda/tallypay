"""A mobile-money provider that fails in the ways real ones do.

It stands for another company's system: the rest of the app only asks it to
charge, asks how a charge went, and receives its callbacks.
"""

import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from tasks import queue

from .models import Charge, Kind, Request, Scenario

DECIDES_AFTER = timedelta(seconds=3)
DECIDES_SLOWLY_AFTER = timedelta(minutes=1)
CALLBACKS = {
    Scenario.DUPLICATE_CALLBACK: 3,
    Scenario.NO_CALLBACK: 0,
    Scenario.SILENT: 0,
}
TOO_MUCH = 500

Callback = Callable[[uuid.UUID, bool], None]
_callbacks: dict[str, Callback] = {}


class NoAnswer(Exception):
    """Nothing came back, so the charge may or may not exist."""


def on_callback(kind: str) -> Callable[[Callback], Callback]:
    def register(callback: Callback) -> Callback:
        _callbacks[kind] = callback
        return callback

    return register


def charge(
    reference: uuid.UUID, amount: int, scenario: str, kind: str = Kind.CHARGE
) -> None:
    """Asks to move money. Asking again with the same reference moves nothing."""
    first = not Request.objects.filter(reference=reference).exists()
    Request.objects.create(reference=reference)
    if first and scenario == Scenario.REQUEST_LOST:
        raise NoAnswer
    with transaction.atomic():
        _record(reference, amount, scenario, kind)
    if first and scenario == Scenario.ANSWER_LOST:
        raise NoAnswer


def _record(reference: uuid.UUID, amount: int, scenario: str, kind: str) -> None:
    delay = DECIDES_SLOWLY_AFTER if scenario == Scenario.SLOW else DECIDES_AFTER
    _, created = Charge.objects.get_or_create(
        reference=reference,
        defaults={
            "kind": kind,
            "amount": amount + (TOO_MUCH if scenario == Scenario.WRONG_AMOUNT else 0),
            "scenario": scenario,
            "decided_at": timezone.now() + delay,
        },
    )
    if created and scenario == Scenario.MOVED_TWICE:
        Charge.objects.create(
            reference=uuid.uuid4(),
            kind=kind,
            amount=amount,
            scenario=Scenario.NORMAL,
            decided_at=timezone.now() + delay,
        )
    if created:
        for _ in range(CALLBACKS.get(Scenario(scenario), 1)):
            queue.add("provider_callback", reference, delay)


def outcome(reference: uuid.UUID) -> bool | None:
    """True if paid, False if declined, None while undecided or unknown."""
    charge = Charge.objects.filter(reference=reference).first()
    if charge is None or charge.decided_at > timezone.now():
        return None
    if charge.scenario == Scenario.SILENT:
        return None
    return charge.scenario not in (Scenario.DECLINE, Scenario.LATE_SUCCESS)


def knows(reference: uuid.UUID) -> bool:
    return Charge.objects.filter(reference=reference).exists()


@dataclass(frozen=True)
class Moved:
    reference: uuid.UUID
    kind: str
    amount: int
    at: datetime


def statement(references: Collection[uuid.UUID] | None = None) -> list[Moved]:
    """All the money that moved so far, or only for these references."""
    moved = Charge.objects.filter(decided_at__lte=timezone.now()).exclude(
        scenario__in=[Scenario.DECLINE, Scenario.NOT_IN_STATEMENT]
    )
    if references is not None:
        moved = moved.filter(reference__in=references)
    return [
        Moved(c.reference, c.kind, c.amount, c.decided_at)
        for c in moved.order_by("decided_at", "id")
    ]


@queue.handles("provider_callback")
def _call_back(reference: uuid.UUID, attempt: int) -> None:
    paid = outcome(reference)
    if paid is None:
        # QStash may be a moment early.
        queue.add("provider_callback", reference, timedelta(seconds=2), attempt + 1)
        return
    kind = Charge.objects.get(reference=reference).kind
    _callbacks[kind](reference, paid)

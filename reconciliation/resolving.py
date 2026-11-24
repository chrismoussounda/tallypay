"""What can be done about a discrepancy: one person proposes, another approves."""

import uuid
from datetime import timedelta
from functools import partial

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from ledger import books
from ledger.models import Account, Entry
from payments import service as payments
from payments.models import Payment
from provider import client, simulator
from provider.models import Kind, Scenario
from refunds import service as refunds
from tasks import queue

from .models import Discrepancy

Action = Discrepancy.Action
Status = Discrepancy.Status

POLICY = "policy"


class NotPossible(Exception):
    pass


def allowed(found: Discrepancy) -> set[str]:
    if found.kind == Discrepancy.Kind.STUCK:
        return {Action.FAIL}
    if found.booking is None or found.amount < 0:
        return {Action.WRITE_OFF}
    known = Payment.objects.filter(id=found.reference).exists()
    return {Action.RETURN, Action.WRITE_OFF} | ({Action.CREDIT} if known else set())


@transaction.atomic
def propose(found_id: uuid.UUID, action: str, by: User, note: str) -> Discrepancy:
    _check(by)
    found = _lock(found_id, Status.OPEN)
    if not note:
        raise NotPossible("Say why.")
    if action not in allowed(found):
        raise NotPossible(f"Choose among: {', '.join(sorted(allowed(found)))}.")
    found.action, found.proposed_by, found.note = action, by.email, note
    found.proposal += 1
    found.status = Status.PROPOSED
    found.save()
    return found


@transaction.atomic
def reject(found_id: uuid.UUID, by: User, proposal: int) -> Discrepancy:
    _check(by)
    found = _proposed(found_id, proposal)
    found.status, found.action = Status.OPEN, ""
    found.save()
    return found


@transaction.atomic
def approve(found_id: uuid.UUID, by: User, proposal: int) -> Discrepancy:
    _check(by)
    found = _proposed(found_id, proposal)
    if by.email == found.proposed_by:
        raise NotPossible("Someone else must approve it.")
    found.approved_by = by.email
    if found.action == Action.RETURN:
        _start_return(found)
        return found
    entry = None
    if found.action == Action.FAIL:
        (payments if found.operation == Kind.CHARGE else refunds).fail(found.reference)
    elif found.action == Action.CREDIT:
        merchant = Payment.objects.get(id=found.reference).merchant
        entry = _post(found, merchant.account, -found.amount)
    else:
        entry = _post(
            found, books.system("differences", Account.Kind.EXPENSE), -found.amount
        )
    close(found, Action(found.action), entry)
    return found


def give_back_by_policy(found: Discrepancy) -> None:
    """Money taken for a payment that failed always goes back, at once."""
    found.action, found.note = Action.RETURN, "Taken after the payment had failed."
    found.proposed_by = found.approved_by = POLICY
    _start_return(found)


def close(found: Discrepancy, action: Action, entry: Entry | None = None) -> None:
    found.action, found.resolution = action, entry
    found.status, found.resolved_at = Status.RESOLVED, timezone.now()
    found.save()


def _post(found: Discrepancy, against: Account, amount: int) -> Entry:
    """Moves the difference out of suspense, or out of what the provider holds."""
    source = (
        books.system("suspense", Account.Kind.LIABILITY)
        if found.booking
        else books.system("provider", Account.Kind.ASSET)
    )
    # A missing amount is negative: what the provider holds goes down by it.
    moved = -amount
    return books.post(
        f"{found.action} {found.kind} {found.reference}",
        {source: moved, against: -moved},
    )


def _start_return(found: Discrepancy) -> None:
    found.status = Status.RETURNING
    found.save()
    queue.add("return_money", found.id, timedelta())


@queue.handles("return_money")
def _return_money(found_id: uuid.UUID, attempt: int) -> None:
    found = Discrepancy.objects.get(id=found_id)
    if found.status != Status.RETURNING:
        return
    ask = partial(
        simulator.charge, found.id, found.amount, Scenario.NORMAL, Kind.RETURN
    )
    client.process("return_money", found_id, attempt, ask, settle_return)


@simulator.on_callback(Kind.RETURN)
@transaction.atomic
def settle_return(found_id: uuid.UUID, paid: bool) -> None:
    found = Discrepancy.objects.select_for_update().get(id=found_id)
    if found.status != Status.RETURNING:
        return
    if not paid:
        found.status, found.action = Status.OPEN, ""
        found.save()
        return
    provider = books.system("provider", Account.Kind.ASSET)
    close(found, Action.RETURN, _post(found, provider, -found.amount))


def _check(by: User) -> None:
    if not (by.is_operator and by.is_active):
        raise NotPossible("Only an operator can do this.")


def _proposed(found_id: uuid.UUID, proposal: int) -> Discrepancy:
    found = _lock(found_id, Status.PROPOSED)
    if found.proposal != proposal:
        raise NotPossible("Another proposal was made since. Read it first.")
    return found


def _lock(found_id: uuid.UUID, status: Status) -> Discrepancy:
    found = Discrepancy.objects.select_for_update().get(id=found_id)
    if found.status != status:
        raise NotPossible(f"It is {found.status}.")
    return found

"""Compares the provider's statement with our books."""

import logging
import uuid
from collections.abc import Collection
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ledger import books
from ledger.models import Account, Entry
from merchants.models import Merchant
from payments import service as payments
from payments.models import Payment
from provider import simulator
from provider.models import Kind
from refunds import service as refunds
from refunds.models import Refund

from . import resolving
from .models import Discrepancy, StatementLine

log = logging.getLogger(__name__)

# A statement can be late, so what is not there yet is not missing yet.
STATEMENT_LAG = timedelta(hours=1)
# Longer than the app keeps on asking the provider.
STUCK_AFTER = timedelta(hours=8)


def run(demo: Merchant | None = None) -> int:
    """Returns how many discrepancies of shops wait for someone.

    Of a demo, only its own money is compared, and nothing is late.
    """
    started = timezone.now()
    only = resolving.references_of(demo) if demo else None
    _import(only)
    waiting = StatementLine.objects.filter(matched_at=None).order_by("moved_at", "id")
    if only is not None:
        waiting = waiting.filter(reference__in=only)
    for line_id in list(waiting.values_list("id", flat=True)):
        try:
            _match(line_id)
        except Exception:
            # Left for the next run, so that the others are still compared.
            log.exception("Statement line %s was not matched", line_id)
    if demo:
        _find_missing(started, demo)
    else:
        _find_missing(started - STATEMENT_LAG)
        _find_stuck()
    waiting_for = resolving.of_shops()
    return waiting_for.exclude(status=Discrepancy.Status.RESOLVED).count()


def _import(only: Collection[uuid.UUID] | None) -> None:
    StatementLine.objects.bulk_create(
        [
            StatementLine(
                reference=moved.reference,
                kind=moved.kind,
                amount=moved.amount,
                moved_at=moved.at,
            )
            for moved in simulator.statement(only)
        ],
        ignore_conflicts=True,
    )


@transaction.atomic
def _match(line_id: int) -> None:
    line = StatementLine.objects.select_for_update().get(id=line_id)
    if line.matched_at:
        return
    if line.kind == Kind.CHARGE:
        _match_payment(line)
    elif line.kind == Kind.REFUND:
        _match_refund(line)
    else:
        resolving.settle_return(line.reference, True)
    _close_those_explained(line)
    line.matched_at = timezone.now()
    line.save(update_fields=["matched_at"])


def _match_payment(line: StatementLine) -> None:
    payment = Payment.objects.filter(id=line.reference).first()
    if payment is None:
        _report(Discrepancy.Kind.UNKNOWN, line, line.amount)
        return
    # The statement is the last word on what the provider never answered.
    try:
        payments.succeed(payment.id)
    except payments.AlreadySettled:
        found = _report(Discrepancy.Kind.AFTER_FAILURE, line, line.amount)
        resolving.give_back_by_policy(found)
        return
    if line.amount != payment.amount:
        _report(Discrepancy.Kind.WRONG_AMOUNT, line, line.amount - payment.amount)


def _match_refund(line: StatementLine) -> None:
    refund = Refund.objects.filter(id=line.reference).first()
    if refund is None:
        _report(Discrepancy.Kind.UNKNOWN, line, -line.amount)
        return
    try:
        refunds.succeed(refund.id)
    except refunds.AlreadySettled:
        _report(Discrepancy.Kind.AFTER_FAILURE, line, -line.amount)
        return
    if line.amount != refund.amount:
        _report(Discrepancy.Kind.WRONG_AMOUNT, line, refund.amount - line.amount)


def _report(kind: Discrepancy.Kind, line: StatementLine, amount: int) -> Discrepancy:
    found = Discrepancy(
        kind=kind, reference=line.reference, operation=line.kind, amount=amount
    )
    found.booking = _to_suspense(found, amount)
    found.save()
    return found


def _to_suspense(found: Discrepancy, amount: int) -> Entry:
    return books.post(
        f"Unexplained {found.kind} {found.reference}",
        {
            books.system("provider", Account.Kind.ASSET): amount,
            books.system("suspense", Account.Kind.LIABILITY): -amount,
        },
    )


def _close_those_explained(line: StatementLine) -> None:
    """What was missing or stuck, and has now appeared."""
    for found in Discrepancy.objects.select_for_update().filter(
        kind__in=[Discrepancy.Kind.MISSING, Discrepancy.Kind.STUCK],
        reference=line.reference,
    ):
        if found.status != Discrepancy.Status.RESOLVED:
            resolving.close(found, Discrepancy.Action.MATCHED)
        elif found.resolution:
            # It was written off, so its money is in the books no more.
            _report(Discrepancy.Kind.LATE, line, -found.amount)


def _find_missing(before: datetime, demo: Merchant | None = None) -> None:
    payments_of = {"merchant": demo} if demo else {}
    refunds_of = {"payment__merchant": demo} if demo else {}
    listed = StatementLine.objects.values("reference")
    for payment in Payment.objects.filter(
        **payments_of,
        status=Payment.Status.SUCCEEDED,
        entry__created_at__lt=before,
    ).exclude(id__in=listed):
        _notice(Discrepancy.Kind.MISSING, payment.id, Kind.CHARGE, -payment.amount)
    for refund in Refund.objects.filter(
        **refunds_of, status=Refund.Status.SUCCEEDED, settled_at__lt=before
    ).exclude(id__in=listed):
        _notice(Discrepancy.Kind.MISSING, refund.id, Kind.REFUND, refund.amount)
    if demo:
        return
    for returned in Discrepancy.objects.filter(
        action=Discrepancy.Action.RETURN,
        status=Discrepancy.Status.RESOLVED,
        resolved_at__lt=before,
    ).exclude(id__in=listed):
        _notice(Discrepancy.Kind.MISSING, returned.id, Kind.RETURN, returned.amount)


def _find_stuck() -> None:
    before = timezone.now() - STUCK_AFTER
    old = Q(status="pending", created_at__lt=before)
    listed = StatementLine.objects.values("reference")
    for payment in Payment.objects.filter(old).exclude(id__in=listed):
        _notice(Discrepancy.Kind.STUCK, payment.id, Kind.CHARGE, 0)
    for refund in Refund.objects.filter(old).exclude(id__in=listed):
        _notice(Discrepancy.Kind.STUCK, refund.id, Kind.REFUND, 0)
    # Settled since, by a callback.
    for found in Discrepancy.objects.filter(kind=Discrepancy.Kind.STUCK).exclude(
        status=Discrepancy.Status.RESOLVED
    ):
        owner = Payment if found.operation == Kind.CHARGE else Refund
        if not owner.objects.filter(id=found.reference, status="pending").exists():
            resolving.close(found, Discrepancy.Action.MATCHED)


def _notice(
    kind: Discrepancy.Kind, reference: uuid.UUID, operation: str, amount: int
) -> None:
    Discrepancy.objects.get_or_create(
        kind=kind,
        reference=reference,
        defaults={"operation": operation, "amount": amount},
    )

import uuid

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ledger import books
from ledger.models import Account, Entry
from merchants import demos
from merchants.models import Merchant
from payments.models import Payment
from provider.models import Scenario
from webhooks import service as webhooks

from .models import Refund


class NotRefundable(Exception):
    """A payment that did not succeed."""


class TooMuch(Exception):
    """More than is left of the payment."""


class NotEnough(Exception):
    """More than the merchant is owed."""


class AlreadySettled(Exception):
    pass


def _set_aside() -> Account:
    return books.system("refunds", Account.Kind.LIABILITY)


def refunded(payment: Payment) -> int:
    """What was given back, or is about to be."""
    counted = payment.refunds.exclude(status=Refund.Status.FAILED)
    total: int | None = counted.aggregate(total=Sum("amount"))["total"]
    return int(total or 0)


@transaction.atomic
def create(
    payment_id: uuid.UUID,
    amount: int,
    scenario: str = Scenario.NORMAL,
    reason: str = Refund.Reason.REQUESTED,
) -> Refund:
    """Sets the money aside at once. The fee of the payment is not given back."""
    payment = Payment.objects.select_related("merchant").get(id=payment_id)
    demos.spend(payment.merchant, scenario)
    # Locked, so that refunds at the same time cannot add up to too much.
    merchant = (
        Merchant.objects.select_for_update(of=("self",))
        .select_related("account")
        .get(id=payment.merchant_id)
    )
    if payment.status != Payment.Status.SUCCEEDED:
        raise NotRefundable
    if amount > payment.amount - refunded(payment):
        raise TooMuch
    if amount > -books.balance(merchant.account):
        raise NotEnough
    refund = Refund(payment=payment, amount=amount, scenario=scenario, reason=reason)
    refund.hold = books.post(
        f"Refund {refund.id}", {merchant.account: amount, _set_aside(): -amount}
    )
    refund.save()
    _tell(merchant, refund, "created")
    return refund


@transaction.atomic
def succeed(refund_id: uuid.UUID) -> Refund:
    refund, done = _lock(refund_id, Refund.Status.SUCCEEDED)
    if done:
        return refund
    provider = books.system("provider", Account.Kind.ASSET)
    entry = books.post(
        f"Refund {refund.id} paid out",
        {_set_aside(): refund.amount, provider: -refund.amount},
    )
    return _settle(refund, Refund.Status.SUCCEEDED, entry)


@transaction.atomic
def fail(refund_id: uuid.UUID) -> Refund:
    """Gives the money back to the merchant."""
    refund, done = _lock(refund_id, Refund.Status.FAILED)
    if done:
        return refund
    entry = books.reverse(refund.hold, f"Refund {refund.id} failed")
    return _settle(refund, Refund.Status.FAILED, entry)


def _lock(refund_id: uuid.UUID, wanted: Refund.Status) -> tuple[Refund, bool]:
    refund = (
        Refund.objects.select_for_update(of=("self",))
        .select_related("payment__merchant", "hold")
        .get(id=refund_id)
    )
    if refund.status not in (Refund.Status.PENDING, wanted):
        raise AlreadySettled(refund.status)
    return refund, refund.status == wanted


def _settle(refund: Refund, status: Refund.Status, entry: Entry) -> Refund:
    refund.status = status
    refund.settlement = entry
    refund.settled_at = timezone.now()
    refund.save(update_fields=["status", "settlement", "settled_at"])
    _tell(refund.payment.merchant, refund, status.value)
    return refund


def _tell(merchant: Merchant, refund: Refund, what: str) -> None:
    webhooks.emit(
        merchant,
        f"refund.{what}",
        {
            "id": str(refund.id),
            "payment_id": str(refund.payment_id),
            "amount": refund.amount,
            "currency": "XAF",
            "reason": refund.reason,
            "status": refund.status,
        },
    )

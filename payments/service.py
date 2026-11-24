import uuid

from django.db import transaction

from ledger import books
from ledger.models import Account
from merchants.models import Merchant
from provider.models import Scenario

from .models import Payment, PaymentEvent

FEE_PERCENT = 2


class AlreadySettled(Exception):
    pass


def fee_on(amount: int) -> int:
    """Rounded down to a whole franc, in the merchant's favour."""
    return amount * FEE_PERCENT // 100


def _account(code: str, kind: Account.Kind) -> Account:
    return Account.objects.get_or_create(
        code=code, defaults={"name": code.title(), "kind": kind}
    )[0]


@transaction.atomic
def create(
    merchant: Merchant,
    amount: int,
    reference: str,
    scenario: str = Scenario.NORMAL,
) -> Payment:
    payment = Payment.objects.create(
        merchant=merchant, amount=amount, reference=reference, scenario=scenario
    )
    payment.events.create(kind=PaymentEvent.Kind.CREATED)
    return payment


@transaction.atomic
def succeed(payment_id: uuid.UUID) -> Payment:
    """Puts the payment in the books, once however many times it is called."""
    payment, done = _lock(payment_id, Payment.Status.SUCCEEDED)
    if done:
        return payment
    fee = fee_on(payment.amount)
    amounts = {
        _account("provider", Account.Kind.ASSET): payment.amount,
        payment.merchant.account: fee - payment.amount,
    }
    if fee:
        amounts[_account("fees", Account.Kind.INCOME)] = -fee
    payment.entry = books.post(f"Payment {payment.id}", amounts)
    return _settle(payment, Payment.Status.SUCCEEDED)


@transaction.atomic
def fail(payment_id: uuid.UUID) -> Payment:
    payment, done = _lock(payment_id, Payment.Status.FAILED)
    return payment if done else _settle(payment, Payment.Status.FAILED)


def _lock(payment_id: uuid.UUID, wanted: Payment.Status) -> tuple[Payment, bool]:
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .select_related("merchant__account")
        .get(id=payment_id)
    )
    if payment.status not in (Payment.Status.PENDING, wanted):
        raise AlreadySettled(payment.status)
    return payment, payment.status == wanted


def _settle(payment: Payment, status: Payment.Status) -> Payment:
    payment.status = status
    payment.save(update_fields=["status", "entry"])
    payment.events.create(kind=PaymentEvent.Kind(status.value))
    return payment

"""Takes a payment from pending to settled, whatever the provider does."""

import logging
import uuid
from datetime import timedelta

from provider import simulator
from tasks import queue
from tasks.models import Task

from . import service
from .models import Payment

log = logging.getLogger(__name__)

# When to look again, after each try. Then it is left to reconciliation.
RETRIES = [timedelta(seconds=seconds) for seconds in (10, 30, 120, 600, 3600, 6 * 3600)]


def start(payment: Payment) -> None:
    queue.add("process_payment", payment.id, timedelta())


def _look_again(payment_id: uuid.UUID, attempt: int) -> None:
    planned = Task.objects.filter(
        kind="process_payment", subject=payment_id, attempt=attempt + 1
    )
    # A try that crashed runs again, and must not plan a second one.
    if not planned.exists():
        queue.add("process_payment", payment_id, RETRIES[attempt - 1], attempt + 1)


@queue.handles("process_payment")
def _process(payment_id: uuid.UUID, attempt: int) -> None:
    payment = Payment.objects.get(id=payment_id)
    if payment.status != Payment.Status.PENDING:
        return
    # Before asking, so that a crash while asking cannot lose the payment.
    if attempt <= len(RETRIES):
        _look_again(payment_id, attempt)
    else:
        log.warning("Payment %s is left to reconciliation", payment_id)
    if not simulator.knows(payment_id):
        try:
            # The payment's own id, so asking twice never charges twice.
            simulator.charge(payment_id, payment.amount, payment.scenario)
        except simulator.NoAnswer:
            return
    paid = simulator.outcome(payment_id)
    if paid is not None:
        settle(payment_id, paid)


@simulator.on_callback
def settle(payment_id: uuid.UUID, paid: bool) -> None:
    try:
        (service.succeed if paid else service.fail)(payment_id)
    except service.AlreadySettled as status:
        log.error("Payment %s is %s, but the provider disagrees", payment_id, status)

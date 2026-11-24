"""Takes a payment from pending to settled, whatever the provider does."""

import logging
import uuid
from datetime import timedelta
from functools import partial

from provider import client, simulator
from provider.models import Kind
from tasks import queue

from . import service
from .models import Payment

log = logging.getLogger(__name__)


def start(payment: Payment) -> None:
    queue.add("process_payment", payment.id, timedelta())


@queue.handles("process_payment")
def _process(payment_id: uuid.UUID, attempt: int) -> None:
    payment = Payment.objects.get(id=payment_id)
    if payment.status != Payment.Status.PENDING:
        return
    # The payment's own id, so asking twice never charges twice.
    ask = partial(_charge, payment)
    client.process("process_payment", payment_id, attempt, ask, settle)


def _charge(payment: Payment) -> None:
    simulator.charge(payment.id, payment.amount, payment.scenario)


@simulator.on_callback(Kind.CHARGE)
def settle(payment_id: uuid.UUID, paid: bool) -> None:
    try:
        (service.succeed if paid else service.fail)(payment_id)
    except service.AlreadySettled as status:
        log.error("Payment %s is %s, but the provider disagrees", payment_id, status)

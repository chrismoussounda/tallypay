"""Takes a refund from pending to settled, whatever the provider does."""

import logging
import uuid
from datetime import timedelta
from functools import partial

from provider import client, simulator
from provider.models import Kind
from tasks import queue

from . import service
from .models import Refund

log = logging.getLogger(__name__)


def start(refund: Refund) -> None:
    queue.add("process_refund", refund.id, timedelta())


@queue.handles("process_refund")
def _process(refund_id: uuid.UUID, attempt: int) -> None:
    refund = Refund.objects.get(id=refund_id)
    if refund.status != Refund.Status.PENDING:
        return
    ask = partial(_give_back, refund)
    client.process("process_refund", refund_id, attempt, ask, settle)


def _give_back(refund: Refund) -> None:
    simulator.charge(refund.id, refund.amount, refund.scenario, Kind.REFUND)


@simulator.on_callback(Kind.REFUND)
def settle(refund_id: uuid.UUID, paid: bool) -> None:
    try:
        (service.succeed if paid else service.fail)(refund_id)
    except service.AlreadySettled as status:
        log.error("Refund %s is %s, but the provider disagrees", refund_id, status)

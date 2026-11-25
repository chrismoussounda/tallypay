import json
import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from merchants import demos
from merchants.models import Merchant
from tasks import queue

from . import sender, signing
from .models import Delivery, Endpoint, Event, Try

MAX_ENDPOINTS = 5
# When to try again, after each failure. Then the delivery has failed.
RETRIES = [
    timedelta(seconds=seconds) for seconds in (10, 60, 600, 3600, 6 * 3600, 24 * 3600)
]

# Each wakes the app, so a demo has fewer of them.
DEMO_RETRIES = RETRIES[:3]


class TooMany(Exception):
    pass


class UnderWay(Exception):
    """A delivery that is still being tried."""


class Removed(Exception):
    """A delivery to an endpoint that was removed."""


@transaction.atomic
def add_endpoint(merchant: Merchant, url: str) -> Endpoint:
    sender.check(url)
    if hasattr(merchant, "demo"):
        raise sender.Refused("A demo has an address of its own, and no other.")
    # Locked, so that two requests at once cannot both be the last allowed.
    Merchant.objects.select_for_update().get(id=merchant.id)
    if merchant.endpoints.filter(removed_at=None).count() >= MAX_ENDPOINTS:
        raise TooMany
    return Endpoint.objects.create(merchant=merchant, url=url)


def remove_endpoint(endpoint: Endpoint) -> None:
    endpoint.removed_at = timezone.now()
    endpoint.save(update_fields=["removed_at"])


@transaction.atomic
def emit(merchant: Merchant, kind: str, data: dict[str, Any]) -> Event:
    """Committed with what it tells about, so neither exists without the other."""
    event = Event.objects.create(merchant=merchant, type=kind, data=data)
    for endpoint in merchant.endpoints.filter(removed_at=None):
        delivery = Delivery.objects.create(event=event, endpoint=endpoint)
        queue.add("deliver_webhook", delivery.id, timedelta())
    return event


def body_of(event: Event) -> bytes:
    return json.dumps(
        {
            "id": str(event.id),
            "type": event.type,
            "created_at": event.created_at.isoformat(),
            "data": event.data,
        }
    ).encode()


@queue.handles("deliver_webhook")
def _deliver(delivery_id: uuid.UUID, attempt: int) -> None:
    delivery = Delivery.objects.select_related("event", "endpoint").get(id=delivery_id)
    # Already tried, when the app crashed before saying so to the queue.
    if delivery.status != Delivery.Status.PENDING or delivery.tries >= attempt:
        return
    if delivery.endpoint.removed_at:
        _record(delivery_id, attempt, None, "The endpoint was removed.", final=True)
        return
    status, error = _send(delivery)
    _record(delivery_id, attempt, status, error)


def _send(delivery: Delivery) -> tuple[int | None, str]:
    event, endpoint = delivery.event, delivery.endpoint
    body = body_of(event)
    # The event's id, the same at every try, so that a merchant can spot a repeat.
    headers = signing.headers(
        signing.secret_of(endpoint.id), event.id, int(timezone.now().timestamp()), body
    )
    try:
        return sender.send(endpoint.url, headers, body), ""
    except (sender.Refused, sender.Unreachable) as error:
        return None, str(error)


@transaction.atomic
def _record(
    delivery_id: uuid.UUID,
    attempt: int,
    status: int | None,
    error: str,
    final: bool = False,
) -> None:
    delivery = (
        Delivery.objects.select_for_update(of=("self",))
        .select_related("endpoint__merchant__demo")
        .get(id=delivery_id)
    )
    if delivery.tries >= attempt:
        return
    delivery.tries = attempt
    Try.objects.create(delivery=delivery, status_code=status, error=error)
    demo = hasattr(delivery.endpoint.merchant, "demo")
    retries = DEMO_RETRIES if demo else RETRIES
    failures = attempt - delivery.tries_before
    if status is not None and 200 <= status < 300:
        delivery.status = Delivery.Status.DELIVERED
    elif final or failures > len(retries):
        delivery.status = Delivery.Status.FAILED
    else:
        queue.add("deliver_webhook", delivery_id, retries[failures - 1], attempt + 1)
    delivery.save(update_fields=["tries", "status"])


@transaction.atomic
def replay(delivery_id: uuid.UUID) -> Delivery:
    """Sends again what was delivered or had failed, with all its retries."""
    delivery = (
        Delivery.objects.select_for_update(of=("self",))
        .select_related("event", "endpoint__merchant")
        .get(id=delivery_id)
    )
    if delivery.status == Delivery.Status.PENDING:
        raise UnderWay
    if delivery.endpoint.removed_at:
        raise Removed
    demos.spend(delivery.endpoint.merchant)
    delivery.status = Delivery.Status.PENDING
    delivery.tries_before = delivery.tries
    delivery.save(update_fields=["status", "tries_before"])
    queue.add("deliver_webhook", delivery.id, timedelta(), delivery.tries + 1)
    return delivery

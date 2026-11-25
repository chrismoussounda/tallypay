"""A shop of their own for each visitor. Closed after a day, never deleted."""

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from merchants import demos, keys
from merchants.models import Demo, Merchant
from payments import service as payments
from payments.models import Payment, PaymentEvent
from provider.models import Charge, Scenario
from reconciliation import matching
from tasks import queue
from webhooks import service as webhooks
from webhooks.models import Endpoint

PER_ADDRESS = 3
PER_DAY = 50
PEOPLE = [
    ("owner", "Ange", User.Role.OWNER),
    ("proposer", "Prisca", User.Role.OPERATOR),
    ("approver", "Divin", User.Role.OPERATOR),
]
# Days ago, amount, reference, and what the provider did.
SAMPLE = [
    (27, 15_000, "CMD-1001", Scenario.NORMAL),
    (25, 42_500, "CMD-1002", Scenario.NORMAL),
    (22, 8_000, "CMD-1003", Scenario.DECLINE),
    (21, 27_000, "CMD-1004", Scenario.NORMAL),
    (18, 63_000, "CMD-1005", Scenario.NORMAL),
    (15, 12_000, "CMD-1006", Scenario.SILENT),
    (13, 35_000, "CMD-1007", Scenario.NORMAL),
    (10, 20_000, "CMD-1008", Scenario.WRONG_AMOUNT),
    (8, 54_000, "CMD-1009", Scenario.NORMAL),
    (6, 18_500, "CMD-1010", Scenario.NOT_IN_STATEMENT),
    (4, 9_500, "CMD-1011", Scenario.DECLINE),
    (2, 31_000, "CMD-1012", Scenario.NORMAL),
    (1, 47_000, "CMD-1013", Scenario.NORMAL),
]


class TooMany(Exception):
    """From this address, in the last hour."""


class Full(Exception):
    """No more demos today."""


def _hidden(address: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, b"demo:" + address.encode(), hashlib.sha256).hexdigest()


@transaction.atomic
def open_one(address: str) -> Demo:
    demos.lock()
    now, hidden = timezone.now(), _hidden(address)
    recent = Demo.objects.filter(created_at__gt=now - demos.DAY)
    asked = recent.filter(address=hidden, created_at__gt=now - timedelta(hours=1))
    if asked.count() >= PER_ADDRESS:
        raise TooMany
    if recent.count() >= PER_DAY:
        raise Full
    merchant = keys.create_merchant("Boutique Démo")
    for days, amount, reference, scenario in SAMPLE:
        _sample(merchant, now - timedelta(days=days), amount, reference, scenario)
    demo = Demo.objects.create(merchant=merchant, address=hidden)
    for what, name, role in PEOPLE:
        User.objects.create_user(
            f"{what}-{merchant.id.hex}@demo.invalid",
            None,
            name=name,
            role=role,
            merchant=None if role == User.Role.OPERATOR else merchant,
            demo=demo,
        )
    matching.run(merchant)
    # Last, so that the sample is not sent.
    path = reverse("demo-inbox", args=[merchant.id])
    Endpoint.objects.create(
        merchant=merchant, url=settings.PUBLIC_URL.rstrip("/") + path
    )
    queue.add("close_demo", merchant.id, Demo.LASTS)
    return demo


def _sample(
    merchant: Merchant, at: datetime, amount: int, reference: str, scenario: Scenario
) -> None:
    """Without the provider's delays, nor what wakes the app."""
    payment = Payment.objects.create(
        merchant=merchant,
        amount=amount,
        reference=reference,
        scenario=scenario,
        created_at=at,
    )
    payment.events.create(kind=PaymentEvent.Kind.CREATED, created_at=at)
    more = 500 if scenario == Scenario.WRONG_AMOUNT else 0
    Charge.objects.create(
        reference=payment.id, amount=amount + more, scenario=scenario, decided_at=at
    )
    if scenario == Scenario.DECLINE:
        payments.fail(payment.id, at)
    elif scenario != Scenario.SILENT:
        payments.succeed(payment.id, at)


@queue.handles("close_demo")
@transaction.atomic
def _close(merchant_id: uuid.UUID, attempt: int) -> None:
    demos.lock()
    demo = Demo.objects.get(merchant_id=merchant_id)
    if demo.closed_at:
        return
    demo.closed_at, demo.address = timezone.now(), ""
    demo.save(update_fields=["closed_at", "address"])
    User.objects.filter(demo=demo).update(is_active=False)
    for key in demo.merchant.keys.filter(revoked_at=None):
        keys.revoke(key)
    for endpoint in demo.merchant.endpoints.filter(removed_at=None):
        webhooks.remove_endpoint(endpoint)

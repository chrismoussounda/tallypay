import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone
from pytest_django.fixtures import Settings

from merchants import keys
from merchants.models import Merchant
from payments import processing
from payments import service as payments
from provider.models import Scenario
from tasks import queue
from tasks.models import Task
from webhooks import sender, service, signing
from webhooks.models import Delivery, Event, Try

from .clock import Clock
from .conftest import Together
from .receiver import Receiver


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(timezone, "now", clock)
    return clock


@pytest.fixture
def shop(db: None) -> Merchant:
    return keys.create_merchant("Shop")


@pytest.fixture
def server(settings: Settings) -> Iterator[Receiver]:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    with Receiver().running() as server:
        yield server


def emit(shop: Merchant) -> Delivery:
    event = service.emit(shop, "payment.succeeded", {"amount": 100})
    return event.deliveries.get()


@pytest.mark.parametrize(
    ("scenario", "kind"),
    [(Scenario.NORMAL, "payment.succeeded"), (Scenario.DECLINE, "payment.failed")],
)
def test_the_merchant_is_told_once_when_a_payment_is_settled(
    shop: Merchant, server: Receiver, clock: Clock, scenario: str, kind: str
) -> None:
    endpoint = service.add_endpoint(shop, server.url)
    payment = payments.create(shop, 10_000, "order-1", scenario)
    processing.start(payment)
    clock.run_until_quiet()
    [(headers, body)] = server.got
    message = json.loads(body)
    assert message["type"] == kind
    assert message["data"]["id"] == str(payment.id)
    assert message["data"]["amount"] == 10_000
    event = Event.objects.get()
    assert headers["webhook-id"] == message["id"] == str(event.id)
    expected = signing.headers(
        signing.secret_of(endpoint.id),
        event.id,
        int(headers["webhook-timestamp"]),
        body,
    )
    assert headers["webhook-signature"] == expected["Webhook-Signature"]


def test_nobody_is_told_of_what_was_rolled_back(shop: Merchant) -> None:
    service.add_endpoint(shop, "https://shop.example/")
    with pytest.raises(RuntimeError), transaction.atomic():
        emit(shop)
        raise RuntimeError("crash")
    assert not Event.objects.exists()
    assert not Task.objects.exists()


def test_a_merchant_is_not_told_about_another(shop: Merchant) -> None:
    service.add_endpoint(keys.create_merchant("Other"), "https://other.example/")
    service.emit(shop, "payment.succeeded", {})
    assert not Delivery.objects.exists()


def test_a_failure_is_tried_again_with_the_same_message(
    shop: Merchant, server: Receiver, clock: Clock
) -> None:
    server.answers = [500, 302, 200]
    service.add_endpoint(shop, server.url)
    delivery = emit(shop)
    started = clock.now
    clock.run_until_quiet()
    delivery.refresh_from_db()
    assert delivery.status == "delivered"
    codes = delivery.history.order_by("id").values_list("status_code", flat=True)
    assert list(codes) == [500, 302, 200]
    assert len({body for _, body in server.got}) == 1
    assert len({h["webhook-id"] for h, _ in server.got}) == 1
    waited = clock.now - started
    assert service.RETRIES[0] + service.RETRIES[1] <= waited < service.RETRIES[2]


def test_after_every_try_it_has_failed_and_can_be_replayed(
    shop: Merchant, server: Receiver, clock: Clock
) -> None:
    tries = len(service.RETRIES) + 1
    server.answers = [500] * (2 * tries)
    service.add_endpoint(shop, server.url)
    delivery = emit(shop)
    for done in (1, 2):
        clock.run_until_quiet()
        delivery.refresh_from_db()
        assert delivery.status == "failed"
        assert len(server.got) == done * tries
        service.replay(delivery.id)
    clock.run_until_quiet()
    delivery.refresh_from_db()
    assert delivery.status == "delivered"
    assert len({h["webhook-id"] for h, _ in server.got}) == 1


def test_what_was_delivered_can_be_replayed_but_not_while_under_way(
    shop: Merchant, server: Receiver, clock: Clock
) -> None:
    service.add_endpoint(shop, server.url)
    delivery = emit(shop)
    with pytest.raises(service.UnderWay):
        service.replay(delivery.id)
    clock.run_until_quiet()
    service.replay(delivery.id)
    clock.run_until_quiet()
    assert len(server.got) == 2


def test_replays_at_the_same_time_send_once(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    service.add_endpoint(shop, "https://shop.example/")
    delivery = emit(shop)
    Task.objects.all().delete()
    Delivery.objects.update(status="failed", tries=7)
    outcomes = together(6, lambda: service.replay(delivery.id))
    assert sum(isinstance(o, service.UnderWay) for o in outcomes) == 5
    assert Task.objects.count() == 1


def test_a_crash_after_sending_does_not_send_or_plan_again(
    shop: Merchant, server: Receiver, clock: Clock
) -> None:
    server.answers = [500]
    service.add_endpoint(shop, server.url)
    emit(shop)
    task = Task.objects.get()
    assert queue.run(task.id)
    # As if the app had crashed before marking the task done.
    Task.objects.filter(id=task.id).update(done_at=None, claimed_until=None)
    assert queue.run(task.id)
    assert len(server.got) == 1
    assert Task.objects.count() == 2


def test_the_same_try_recorded_twice_counts_once(shop: Merchant) -> None:
    service.add_endpoint(shop, "https://shop.example/")
    delivery = emit(shop)
    for _ in range(2):
        service._record(delivery.id, 1, 500, "")
    assert Try.objects.count() == 1
    assert Task.objects.filter(attempt=2).count() == 1


def test_a_removed_endpoint_gets_nothing_more(
    shop: Merchant, server: Receiver, clock: Clock
) -> None:
    endpoint = service.add_endpoint(shop, server.url)
    delivery = emit(shop)
    service.remove_endpoint(endpoint)
    clock.run_until_quiet()
    delivery.refresh_from_db()
    assert delivery.status == "failed"
    assert server.got == []
    with pytest.raises(service.Removed):
        service.replay(delivery.id)
    service.emit(shop, "payment.succeeded", {})
    assert Delivery.objects.count() == 1


def test_an_address_that_became_internal_gets_nothing(
    shop: Merchant, clock: Clock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False

    def refuse(*args: Any) -> str:
        raise sender.Refused("The address is not a public one.")

    monkeypatch.setattr(sender, "_resolve", refuse)
    service.add_endpoint(shop, "https://shop.example/")
    emit(shop)
    queue.run_due()
    assert Try.objects.get().error == "The address is not a public one."


def test_endpoints_are_limited_even_when_added_at_the_same_time(
    together: Together,
) -> None:
    shop = keys.create_merchant("Shop")
    outcomes = together(
        8, lambda: service.add_endpoint(shop, "https://shop.example/").url
    )
    assert sum(isinstance(o, str) for o in outcomes) == service.MAX_ENDPOINTS
    assert sum(isinstance(o, service.TooMany) for o in outcomes) == 3

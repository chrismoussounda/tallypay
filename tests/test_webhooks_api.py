from typing import Any

import pytest
from django.test import Client
from pytest_django.fixtures import Settings

from merchants import keys
from tasks.models import Task
from webhooks import service, signing
from webhooks.models import Delivery

URL = "https://shop.example/hook"


class Shop:
    def __init__(self) -> None:
        self.merchant = keys.create_merchant("Shop")
        self.key = keys.issue(self.merchant)

    def call(self, method: str, path: str, body: Any = None) -> Any:
        return getattr(Client(), method)(
            f"/v1/{path}",
            body,
            content_type="application/json",
            headers={"Authorization": f"Bearer {self.key}"},
        )

    def get(self, path: str) -> Any:
        return Client().get(
            f"/v1/{path}", headers={"Authorization": f"Bearer {self.key}"}
        )

    def add(self, url: str = URL) -> Any:
        return self.call("post", "webhook-endpoints", {"url": url})

    def emit(self) -> str:
        event = service.emit(self.merchant, "payment.succeeded", {"amount": 5})
        return str(event.deliveries.get().id)


@pytest.fixture
def shop(db: None) -> Shop:
    return Shop()


def test_an_endpoint_is_added_listed_and_removed(shop: Shop) -> None:
    added = shop.add()
    assert added.status_code == 201
    endpoint = shop.merchant.endpoints.get()
    assert added.json()["secret"] == signing.secret_of(endpoint.id)
    [listed] = shop.get("webhook-endpoints").json()
    assert listed["url"] == URL
    assert "secret" not in listed
    path = f"webhook-endpoints/{listed['id']}"
    assert shop.call("delete", path).status_code == 204
    assert shop.call("delete", path).status_code == 404
    assert shop.get("webhook-endpoints").json() == []


@pytest.mark.parametrize("url", ["", "http://shop.example/", "nonsense", "x" * 501])
def test_an_address_that_is_not_allowed_is_refused(
    shop: Shop, settings: Settings, url: str
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False
    refused = shop.add(url)
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"


def test_there_is_a_limit_of_endpoints(shop: Shop) -> None:
    codes = [shop.add().status_code for _ in range(service.MAX_ENDPOINTS + 1)]
    assert codes == [201] * service.MAX_ENDPOINTS + [409]


def test_a_failed_delivery_is_found_read_and_replayed(shop: Shop) -> None:
    shop.add()
    failed, pending = shop.emit(), shop.emit()
    service._record(Delivery.objects.get(id=failed).id, 1, 500, "", final=True)
    found = shop.get("webhook-deliveries?status=failed").json()["data"]
    assert [d["id"] for d in found] == [failed]
    assert found[0]["type"] == "payment.succeeded"
    read = shop.get(f"webhook-deliveries/{failed}").json()
    assert read["data"] == {"amount": 5}
    assert read["history"][0]["status_code"] == 500
    Task.objects.all().delete()
    replayed = shop.call("post", f"webhook-deliveries/{failed}/replay")
    assert replayed.status_code == 202
    assert replayed.json()["status"] == "pending"
    assert Task.objects.get().attempt == 2
    again = shop.call("post", f"webhook-deliveries/{pending}/replay")
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"


def test_deliveries_are_read_page_by_page(shop: Shop) -> None:
    shop.add()
    ids = [shop.emit() for _ in range(3)]
    first = shop.get("webhook-deliveries?limit=2").json()
    assert first["has_more"]
    last = first["data"][-1]["id"]
    rest = shop.get(f"webhook-deliveries?limit=2&after={last}").json()
    assert [d["id"] for d in first["data"] + rest["data"]] == ids[::-1]
    assert not rest["has_more"]


def test_a_merchant_never_reaches_what_belongs_to_another(shop: Shop) -> None:
    endpoint = shop.add().json()["id"]
    delivery = shop.emit()
    other = Shop()
    assert other.get("webhook-endpoints").json() == []
    assert other.get("webhook-deliveries").json()["data"] == []
    assert other.get(f"webhook-deliveries/{delivery}").status_code == 404
    assert other.get(f"webhook-deliveries?after={delivery}").status_code == 404
    path = f"webhook-deliveries/{delivery}/replay"
    assert other.call("post", path).status_code == 404
    path = f"webhook-endpoints/{endpoint}"
    assert other.call("delete", path).status_code == 404
    assert shop.merchant.endpoints.get().removed_at is None


@pytest.mark.django_db
def test_every_route_needs_a_key() -> None:
    for path in ("webhook-endpoints", "webhook-deliveries"):
        assert Client().get(f"/v1/{path}").status_code == 401

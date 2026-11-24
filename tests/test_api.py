import re
import uuid
from typing import Any

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.utils import timezone

from merchants import keys
from merchants.models import ApiKey
from payments import service
from payments.models import Payment
from tasks.models import Task

from .conftest import Together

NEW = {"amount": 10_000, "reference": "order-1"}


class Shop:
    def __init__(self) -> None:
        self.merchant = keys.create_merchant("Shop")
        self.key = keys.issue(self.merchant)

    def pay(self, body: Any = NEW, key: str | None = "k") -> Any:
        headers = {"Authorization": f"Bearer {self.key}"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return Client().post(
            "/v1/payments", body, content_type="application/json", headers=headers
        )

    def get(self, path: str) -> Any:
        return Client().get(
            f"/v1/payments{path}", headers={"Authorization": f"Bearer {self.key}"}
        )


@pytest.fixture
def shop(db: None) -> Shop:
    return Shop()


def test_a_payment_is_created_and_read_back(shop: Shop) -> None:
    created = shop.pay()
    assert created.status_code == 201
    body = created.json()
    assert body["amount"] == 10_000
    assert body["currency"] == "XAF"
    assert body["status"] == "pending"
    service.succeed(uuid.UUID(body["id"]))
    read = shop.get(f"/{body['id']}").json()
    assert read["status"] == "succeeded"
    assert read["refunded"] == 0
    assert [e["kind"] for e in read["events"]] == ["created", "succeeded"]


def test_a_payment_is_handed_to_the_provider_once(shop: Shop) -> None:
    bodies = [shop.pay(NEW | {"scenario": "slow"}).json() for _ in range(2)]
    assert bodies[0]["scenario"] == "slow"
    task = Task.objects.get()
    assert task.kind == "process_payment"
    assert str(task.subject) == bodies[0]["id"]


def test_a_retry_gets_the_same_answer_and_says_so(shop: Shop) -> None:
    first, again = shop.pay(), shop.pay()
    assert again.status_code == 201
    assert again.json() == first.json()
    assert "Idempotent-Replayed" not in first
    assert again["Idempotent-Replayed"] == "true"
    assert Payment.objects.count() == 1


def test_the_same_key_for_another_request_is_refused(shop: Shop) -> None:
    shop.pay()
    refused = shop.pay(NEW | {"amount": 5})
    assert refused.status_code == 409
    assert refused.json()["code"] == "idempotency_key_reused"


@pytest.mark.parametrize("key", [None, "", "a b", "é", "x" * 256])
def test_a_key_is_needed(shop: Shop, key: str | None) -> None:
    assert shop.pay(key=key).status_code == 400
    assert not Payment.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        "not json",
        NEW | {"amount": 0},
        NEW | {"amount": -5},
        NEW | {"amount": 10.5},
        NEW | {"amount": "100"},
        NEW | {"amount": True},
        NEW | {"amount": 10_000_001},
        NEW | {"reference": ""},
        NEW | {"reference": "x" * 65},
        NEW | {"reference": "a\x00b"},
        NEW | {"reference": "\ud800"},
        NEW | {"merchant": "someone"},
        NEW | {"scenario": "unknown"},
    ],
)
def test_a_request_that_makes_no_sense_is_refused(shop: Shop, body: Any) -> None:
    answer = shop.pay(body)
    assert answer.status_code in (400, 422)
    assert answer.json()["code"] == "invalid_request"
    assert not Payment.objects.exists()
    # The key was not used up.
    assert shop.pay().status_code == 201


def test_without_a_valid_key_nothing_is_shown(shop: Shop) -> None:
    paid = shop.pay().json()["id"]
    ApiKey.objects.update(revoked_at="2026-01-01T00:00:00Z")
    for answer in (shop.pay(), shop.get(""), shop.get(f"/{paid}")):
        assert answer.status_code == 401
        assert answer.json()["code"] == "unauthorized"
    assert Client().get("/v1/payments").status_code == 401


def test_a_merchant_never_sees_the_payments_of_another(shop: Shop) -> None:
    paid = shop.pay().json()["id"]
    other = Shop()
    assert other.get(f"/{paid}").status_code == 404
    assert other.get(f"?after={paid}").status_code == 404
    assert other.get("").json() == {"data": [], "has_more": False}


def test_the_list_is_read_page_by_page_newest_first(shop: Shop) -> None:
    ids = [shop.pay(key=f"k{i}").json()["id"] for i in range(5)]
    seen: list[str] = []
    after = ""
    while True:
        page = shop.get(f"?limit=2{after}").json()
        seen += [p["id"] for p in page["data"]]
        if not page["has_more"]:
            break
        after = f"&after={seen[-1]}"
    assert seen == ids[::-1]
    assert shop.get("?limit=101").status_code == 422


def test_payments_of_the_same_moment_are_all_listed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = timezone.now()
    with monkeypatch.context() as patch:
        patch.setattr(timezone, "now", lambda: moment)
        ids = {shop.pay(key=f"k{i}").json()["id"] for i in range(3)}
    first = shop.get("?limit=2").json()["data"]
    rest = shop.get(f"?limit=2&after={first[-1]['id']}").json()["data"]
    assert len(first) == 2
    assert {p["id"] for p in first + rest} == ids


def test_the_same_request_many_times_at_once_pays_once(together: Together) -> None:
    shop = Shop()

    def action() -> tuple[int, str]:
        answer = shop.pay()
        return answer.status_code, answer.json()["id"]

    outcomes = together(8, action)
    assert len(set(map(str, outcomes))) == 1
    assert outcomes[0][0] == 201  # type: ignore[index]
    assert Payment.objects.count() == 1


def test_the_documentation_opens_with_its_own_files(client: Client) -> None:
    page = client.get("/v1/docs")
    assert page.status_code == 200
    files = re.findall(r'(?:src|href)="([^"]+)"', page.content.decode())
    assert files
    for file in files:
        assert finders.find(file.removeprefix("/static/")), file
    assert "https:" not in page["Content-Security-Policy"]

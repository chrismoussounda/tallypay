from typing import Any

import pytest
from django.test import Client

from merchants import keys
from payments import service as payments
from refunds import service
from refunds.models import Refund
from tasks.models import Task


class Shop:
    def __init__(self) -> None:
        self.merchant = keys.create_merchant("Shop")
        self.headers = {"Authorization": f"Bearer {keys.issue(self.merchant)}"}
        # So that the fees of the others do not matter.
        payments.succeed(payments.create(self.merchant, 100_000, "float").id)
        self.payment = payments.succeed(
            payments.create(self.merchant, 10_000, "order").id
        )

    def refund(self, body: Any, key: str = "k", payment: Any = None) -> Any:
        return Client().post(
            f"/v1/payments/{payment or self.payment.id}/refunds",
            body,
            content_type="application/json",
            headers=self.headers | {"Idempotency-Key": key},
        )

    def get(self, path: str) -> Any:
        return Client().get(f"/v1/{path}", headers=self.headers)


@pytest.fixture
def shop(db: None) -> Shop:
    return Shop()


def test_a_refund_is_made_read_and_listed(shop: Shop) -> None:
    made = shop.refund({"amount": 4_000, "reason": "duplicate"})
    assert made.status_code == 201
    body = made.json()
    assert body["status"] == "pending"
    assert body["reason"] == "duplicate"
    assert shop.get(f"refunds/{body['id']}").json() == body
    path = f"payments/{shop.payment.id}"
    assert shop.get(f"{path}/refunds").json() == [body]
    assert shop.get(path).json()["refunded"] == 4_000
    task = Task.objects.get(kind="process_refund")
    assert str(task.subject) == body["id"]


def test_a_retry_refunds_once(shop: Shop) -> None:
    first, again = (shop.refund({"amount": 4_000}) for _ in range(2))
    assert first.json() == again.json()
    assert again["Idempotent-Replayed"] == "true"
    assert Refund.objects.count() == 1


def test_the_same_key_for_another_payment_is_refused(shop: Shop) -> None:
    shop.refund({"amount": 4_000})
    other = payments.succeed(payments.create(shop.merchant, 10_000, "o").id)
    refused = shop.refund({"amount": 4_000}, payment=other.id)
    assert refused.json()["code"] == "idempotency_key_reused"


def test_what_cannot_be_refunded_is_refused_and_the_key_still_works(
    shop: Shop,
) -> None:
    refused = shop.refund({"amount": 10_001})
    assert refused.status_code == 409
    assert "left of the payment" in refused.json()["message"]
    assert shop.refund({"amount": 10_000}).status_code == 201
    pending = payments.create(shop.merchant, 100, "o")
    assert shop.refund({"amount": 1}, "p", pending.id).status_code == 409
    for payment in shop.merchant.payments.filter(reference="float"):
        # 98,000 of it is owed, less what the first refund took.
        assert shop.refund({"amount": 100_000}, "b", payment.id).status_code == 409
        with pytest.raises(service.NotEnough):
            service.create(payment.id, 100_000)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"amount": 0},
        {"amount": -1},
        {"amount": 1.5},
        {"amount": "1"},
        {"amount": 1, "reason": "because"},
        {"amount": 1, "payment": "x"},
    ],
)
def test_a_request_that_makes_no_sense_is_refused(shop: Shop, body: Any) -> None:
    assert shop.refund(body).status_code == 422
    assert not Refund.objects.exists()


def test_a_merchant_never_reaches_the_refunds_of_another(shop: Shop) -> None:
    made = shop.refund({"amount": 4_000}).json()["id"]
    other = Shop()
    assert other.get(f"refunds/{made}").status_code == 404
    assert other.get(f"payments/{shop.payment.id}/refunds").status_code == 404
    assert other.refund({"amount": 1}, payment=shop.payment.id).status_code == 404
    path = f"/v1/payments/{shop.payment.id}/refunds"
    assert Client().get(path).status_code == 401

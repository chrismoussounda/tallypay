import threading
import time
from typing import Any

import pytest

from idempotency.models import IdempotencyRecord
from idempotency.service import KeyReused, once
from merchants import keys
from merchants.models import Merchant
from payments import service
from payments.models import Payment

from .conftest import Together

REQUEST: dict[str, Any] = {"amount": 100, "reference": "order"}


def pay(shop: Merchant, key: str = "k", **changes: Any) -> tuple[dict[str, Any], bool]:
    request = REQUEST | changes
    return once(
        shop, key, request, lambda: {"id": str(service.create(shop, **request).id)}
    )


@pytest.fixture
def shop(db: None) -> Merchant:
    return keys.create_merchant("Shop")


def test_a_retry_gets_the_first_answer(shop: Merchant) -> None:
    first, replayed = pay(shop)
    assert not replayed
    assert pay(shop) == (first, True)
    assert Payment.objects.count() == 1


def test_another_key_is_another_payment(shop: Merchant) -> None:
    assert pay(shop, "a") != pay(shop, "b")
    assert Payment.objects.count() == 2


def test_the_same_key_for_something_else_is_refused(shop: Merchant) -> None:
    pay(shop)
    with pytest.raises(KeyReused):
        pay(shop, amount=200)
    assert Payment.objects.count() == 1


def test_two_merchants_may_use_the_same_key(shop: Merchant) -> None:
    assert pay(shop) != pay(keys.create_merchant("Other"))


def test_a_key_whose_action_failed_can_be_used_again(shop: Merchant) -> None:
    def broken() -> dict[str, Any]:
        service.create(shop, **REQUEST)
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        once(shop, "k", REQUEST, broken)
    assert not Payment.objects.exists()
    assert not IdempotencyRecord.objects.exists()
    assert not pay(shop)[1]


def test_the_same_request_many_times_at_once_pays_once(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    outcomes = together(8, lambda: pay(shop))
    assert len({str(o[0]) for o in outcomes if isinstance(o, tuple)}) == 1
    assert (
        sorted(o[1] for o in outcomes if isinstance(o, tuple)) == [False] + [True] * 7
    )
    assert Payment.objects.count() == 1


def test_one_waiting_behind_a_crash_takes_over(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    turn = threading.Lock()
    calls: list[int] = []

    def action() -> dict[str, Any]:
        with turn:
            calls.append(1)
            first = len(calls) == 1
        payment = service.create(shop, **REQUEST)
        if first:
            time.sleep(0.5)
            raise RuntimeError("crash")
        return {"id": str(payment.id)}

    outcomes = together(4, lambda: once(shop, "k", REQUEST, action))
    assert sum(isinstance(o, RuntimeError) for o in outcomes) == 1
    assert len([o for o in outcomes if isinstance(o, tuple)]) == 3
    assert Payment.objects.count() == 1

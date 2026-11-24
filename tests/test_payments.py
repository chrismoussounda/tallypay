import pytest
from django.db import IntegrityError, connection, transaction

from ledger import books
from ledger.models import Entry
from merchants import keys
from merchants.models import Merchant
from payments import service
from payments.models import Payment, PaymentEvent

from .conftest import Together


@pytest.fixture
def shop(db: None) -> Merchant:
    return keys.create_merchant("Shop")


def test_a_payment_waits_and_moves_no_money(shop: Merchant) -> None:
    payment = service.create(shop, 10_000, "order-1")
    assert payment.status == "pending"
    assert not Entry.objects.exists()


def test_success_puts_it_in_the_books_once(shop: Merchant) -> None:
    payment = service.create(shop, 10_000, "order-1")
    for _ in range(3):
        assert service.succeed(payment.id).status == "succeeded"
    assert Entry.objects.count() == 1
    assert books.trial_balance() == {
        "fees": -200,
        f"merchant:{shop.id}": -9_800,
        "provider": 10_000,
    }
    assert [e.kind for e in payment.events.order_by("id")] == ["created", "succeeded"]


def test_failure_moves_no_money(shop: Merchant) -> None:
    payment = service.create(shop, 10_000, "order-1")
    for _ in range(2):
        assert service.fail(payment.id).status == "failed"
    assert not Entry.objects.exists()


def test_a_settled_payment_is_final(shop: Merchant) -> None:
    won, lost = (service.create(shop, 100, "order") for _ in range(2))
    service.succeed(won.id)
    service.fail(lost.id)
    with pytest.raises(service.AlreadySettled):
        service.fail(won.id)
    with pytest.raises(service.AlreadySettled):
        service.succeed(lost.id)


@pytest.mark.parametrize(
    ("amount", "fee"), [(1, 0), (49, 0), (50, 1), (99, 1), (10_000, 200)]
)
def test_the_fee_is_rounded_down(shop: Merchant, amount: int, fee: int) -> None:
    payment = service.create(shop, amount, "order")
    service.succeed(payment.id)
    assert books.balance(shop.account) == fee - amount


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE payments_payment SET amount = 1",
        "UPDATE payments_payment SET status = 'succeeded'",
        "UPDATE payments_payment SET status = 'strange'",
        "UPDATE payments_paymentevent SET kind = 'failed'",
        "DELETE FROM payments_paymentevent",
        "TRUNCATE payments_paymentevent",
        "INSERT INTO payments_payment (id, merchant_id, amount, reference, created_at)"
        " SELECT gen_random_uuid(), merchant_id, 0, 'x', now() FROM payments_payment",
    ],
)
def test_postgres_refuses_to_tamper_with_a_payment(shop: Merchant, sql: str) -> None:
    service.create(shop, 100, "order")
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


def test_postgres_refuses_to_reopen_a_settled_payment(shop: Merchant) -> None:
    payment = service.create(shop, 100, "order")
    service.fail(payment.id)
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(id=payment.id).update(status="pending")


def test_successes_at_the_same_time_count_once(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    payment = service.create(shop, 10_000, "order-1")
    outcomes = together(8, lambda: service.succeed(payment.id).status)
    assert outcomes == ["succeeded"] * 8
    assert Entry.objects.count() == 1
    assert PaymentEvent.objects.filter(kind="succeeded").count() == 1


def test_a_success_and_a_failure_at_the_same_time_leave_one_winner(
    together: Together,
) -> None:
    shop = keys.create_merchant("Shop")
    payment = service.create(shop, 10_000, "order-1")
    actions = iter([service.succeed, service.fail] * 4)
    outcomes = together(8, lambda: next(actions)(payment.id).status)
    won = [o for o in outcomes if isinstance(o, str)]
    assert len(set(won)) == 1
    assert len(won) == 4
    assert all(isinstance(o, str | service.AlreadySettled) for o in outcomes)
    assert Entry.objects.count() == (1 if won[0] == "succeeded" else 0)

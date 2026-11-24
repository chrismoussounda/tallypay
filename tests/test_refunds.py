import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from ledger import books
from merchants import keys
from merchants.models import Merchant
from payments import service as payments
from payments.models import Payment
from provider.models import Charge, Request, Scenario
from refunds import processing, service
from refunds.models import Refund
from webhooks import service as webhooks
from webhooks.models import Event

from .clock import Clock
from .conftest import Together


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(timezone, "now", clock)
    return clock


def paid(shop: Merchant, amount: int = 10_000) -> Payment:
    payment = payments.create(shop, amount, "order")
    return payments.succeed(payment.id)


def new_shop() -> Merchant:
    """Owed enough by another payment for the fee not to matter."""
    shop = keys.create_merchant("Shop")
    paid(shop, 100_000)
    return shop


@pytest.fixture
def shop(db: None) -> Merchant:
    return new_shop()


def totals() -> dict[str, int]:
    return {code.split(":")[0]: n for code, n in books.trial_balance().items()}


@pytest.mark.django_db
def test_the_money_is_set_aside_then_sent_out() -> None:
    shop = keys.create_merchant("Shop")
    payment = paid(shop)
    refund = service.create(payment.id, 4_000)
    held = {"fees": -200, "merchant": -5_800, "provider": 10_000, "refunds": -4_000}
    assert totals() == held
    for _ in range(3):
        assert service.succeed(refund.id).status == "succeeded"
    assert totals() == held | {"provider": 6_000, "refunds": 0}


def test_a_failed_refund_gives_the_money_back_once(shop: Merchant) -> None:
    payment = paid(shop)
    before = totals()
    refund = service.create(payment.id, 4_000)
    for _ in range(3):
        assert service.fail(refund.id).status == "failed"
    assert totals() == before | {"refunds": 0}
    with pytest.raises(service.AlreadySettled):
        service.succeed(refund.id)


def test_parts_never_add_up_to_more_than_the_payment(shop: Merchant) -> None:
    payment = paid(shop)
    service.create(payment.id, 6_000)
    with pytest.raises(service.TooMuch):
        service.create(payment.id, 4_001)
    failed = service.create(payment.id, 4_000)
    with pytest.raises(service.TooMuch):
        service.create(payment.id, 1)
    # What failed can be asked for again.
    service.fail(failed.id)
    service.create(payment.id, 4_000)
    assert service.refunded(payment) == 10_000


@pytest.mark.django_db
def test_a_merchant_cannot_give_back_more_than_it_is_owed() -> None:
    shop = keys.create_merchant("Shop")
    payment = paid(shop)
    # 10,000 was paid, but the merchant is owed 9,800 of it.
    with pytest.raises(service.NotEnough):
        service.create(payment.id, 10_000)
    service.create(payment.id, 9_800)
    assert books.balance(shop.account) == 0


def test_only_what_succeeded_is_refunded(shop: Merchant) -> None:
    pending = payments.create(shop, 100, "order")
    with pytest.raises(service.NotRefundable):
        service.create(pending.id, 100)
    payments.fail(pending.id)
    with pytest.raises(service.NotRefundable):
        service.create(pending.id, 100)


@pytest.mark.parametrize("amount", [0, -5])
def test_a_refund_of_nothing_is_refused(shop: Merchant, amount: int) -> None:
    payment = paid(shop)
    with pytest.raises((books.InvalidEntry, IntegrityError)), transaction.atomic():
        service.create(payment.id, amount)
    assert not Refund.objects.exists()


def test_postgres_refuses_to_tamper_with_a_refund(shop: Merchant) -> None:
    refund = service.create(paid(shop).id, 100)
    service.succeed(refund.id)
    for sql in (
        "UPDATE refunds_refund SET amount = 1",
        "UPDATE refunds_refund SET status = 'pending'",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)


@pytest.mark.parametrize(
    "scenario", [s for s in Scenario.values if s in Scenario.recoverable()]
)
def test_every_scenario_ends_with_one_refund_and_the_right_status(
    shop: Merchant, clock: Clock, scenario: str
) -> None:
    webhooks.add_endpoint(shop, "https://shop.example/")
    refund = service.create(paid(shop).id, 4_000, scenario)
    processing.start(refund)
    clock.run_until_quiet()
    refund.refresh_from_db()
    status = "failed" if scenario == Scenario.DECLINE else "succeeded"
    assert refund.status == status
    assert Charge.objects.filter(kind="refund").count() == 1
    lost = scenario == Scenario.REQUEST_LOST
    assert Request.objects.count() == (2 if lost else 1)
    assert Event.objects.filter(type=f"refund.{status}").count() == 1
    assert totals()["refunds"] == 0


def test_refunds_at_the_same_time_never_add_up_to_too_much(together: Together) -> None:
    payment = paid(new_shop())
    outcomes = together(8, lambda: service.create(payment.id, 3_000))
    assert sum(isinstance(o, Refund) for o in outcomes) == 3
    assert sum(isinstance(o, service.TooMuch) for o in outcomes) == 5


def test_refunds_of_several_payments_at_once_never_overdraw_the_merchant(
    together: Together,
) -> None:
    shop = keys.create_merchant("Shop")
    left = [paid(shop).id for _ in range(4)]
    # Owed 39,200 in all: not enough for four refunds of 10,000.
    outcomes = together(4, lambda: service.create(left.pop(), 10_000))
    assert sum(isinstance(o, Refund) for o in outcomes) == 3
    assert sum(isinstance(o, service.NotEnough) for o in outcomes) == 1
    assert books.balance(shop.account) <= 0


def test_a_success_and_a_failure_at_the_same_time_leave_one_winner(
    together: Together,
) -> None:
    refund = service.create(paid(new_shop()).id, 4_000)
    actions = iter([service.succeed, service.fail] * 4)
    outcomes = together(8, lambda: next(actions)(refund.id).status)
    won = {o for o in outcomes if isinstance(o, str)}
    assert len(won) == 1
    assert all(isinstance(o, str | service.AlreadySettled) for o in outcomes)
    assert totals()["refunds"] == 0

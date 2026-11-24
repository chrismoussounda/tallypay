from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from ledger import books
from ledger.models import Entry
from merchants import keys
from payments import processing, service
from payments.models import Payment
from provider import simulator
from provider.models import Charge, Request, Scenario
from tasks import queue
from tasks.models import Task


class Clock:
    def __init__(self) -> None:
        self.now = timezone.now()

    def __call__(self) -> datetime:
        return self.now

    def run_until_quiet(self) -> None:
        """Jumps from one due task to the next, until none is left."""
        for _ in range(500):
            waiting = Task.objects.filter(done_at=None).order_by("due_at").first()
            if waiting is None:
                return
            after_crash = waiting.claimed_until or waiting.due_at
            self.now = max(self.now, waiting.due_at, after_crash) + timedelta(
                microseconds=1
            )
            queue.run_due()
        raise AssertionError("never quiet")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    clock = Clock()
    monkeypatch.setattr(timezone, "now", clock)
    yield clock


def pay(scenario: str) -> Payment:
    payment = service.create(keys.create_merchant("Shop"), 10_000, "order", scenario)
    processing.start(payment)
    return payment


@pytest.mark.django_db
@pytest.mark.parametrize("scenario", Scenario.values)
def test_every_scenario_ends_with_one_charge_and_the_right_status(
    clock: Clock, scenario: str
) -> None:
    payment = pay(scenario)
    clock.run_until_quiet()
    payment.refresh_from_db()
    declined = scenario == Scenario.DECLINE
    assert payment.status == ("failed" if declined else "succeeded")
    assert Charge.objects.count() == 1
    assert Entry.objects.count() == (0 if declined else 1)
    lost = scenario == Scenario.REQUEST_LOST
    assert Request.objects.count() == (2 if lost else 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("scenario", "settled"), [(Scenario.NORMAL, True), (Scenario.NO_CALLBACK, False)]
)
def test_the_callback_settles_a_payment_before_the_next_look(
    clock: Clock, scenario: str, settled: bool
) -> None:
    payment = pay(scenario)
    for _ in range(2):
        queue.run_due()
        clock.now += timedelta(seconds=4)
    payment.refresh_from_db()
    assert (payment.status == "succeeded") is settled


@pytest.mark.django_db
def test_a_settled_payment_is_not_looked_at_again(clock: Clock) -> None:
    pay(Scenario.NORMAL)
    clock.run_until_quiet()
    assert Task.objects.filter(kind="process_payment").count() == 2


@pytest.mark.django_db
def test_a_lost_answer_is_not_asked_for_again_once_the_provider_knows(
    clock: Clock,
) -> None:
    payment = pay(Scenario.ANSWER_LOST)
    with pytest.raises(simulator.NoAnswer):
        simulator.charge(payment.id, payment.amount, payment.scenario)
    clock.run_until_quiet()
    assert Request.objects.count() == 1


@pytest.mark.django_db
def test_a_provider_that_never_answers_leaves_the_payment_pending(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def silent(*args: Any) -> None:
        Request.objects.create(reference=args[0])
        raise simulator.NoAnswer

    monkeypatch.setattr(simulator, "charge", silent)
    payment = pay(Scenario.NORMAL)
    clock.run_until_quiet()
    payment.refresh_from_db()
    assert payment.status == "pending"
    assert Request.objects.count() == len(processing.RETRIES) + 1


class Crash(Exception):
    pass


@pytest.mark.django_db
def test_crashes_do_not_multiply_the_tries(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    payment = pay(Scenario.NORMAL)

    def crash(*args: Any) -> None:
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(simulator, "charge", crash)
        for _ in range(5):
            queue.run_due()
    assert Task.objects.filter(subject=payment.id).count() == 2


@pytest.mark.django_db
def test_a_provider_that_changes_its_mind_changes_nothing(clock: Clock) -> None:
    payment = pay(Scenario.NORMAL)
    clock.run_until_quiet()
    processing.settle(payment.id, False)
    payment.refresh_from_db()
    assert payment.status == "succeeded"
    assert Entry.objects.count() == 1


CRASH_POINTS: list[tuple[Any, str]] = [
    (simulator, "charge"),
    (simulator, "outcome"),
    (service, "succeed"),
    (service, "fail"),
]


def crashing(real: Callable[..., Any], crashes: Iterator[str]) -> Callable[..., Any]:
    def maybe(*args: Any) -> Any:
        when = next(crashes, "no")
        if when == "before":
            raise Crash
        result = real(*args)
        if when == "after":
            raise Crash
        return result

    return maybe


class AnySession(TestCase):
    @given(
        scenarios=st.lists(st.sampled_from(Scenario.values), min_size=1, max_size=5),
        crashes=st.lists(st.sampled_from(["no", "before", "after"]), max_size=15),
        strays=st.lists(st.integers(min_value=0, max_value=4), max_size=5),
    )
    @settings(max_examples=100, deadline=None)
    def test_charges_once_and_keeps_the_books_balanced(
        self, scenarios: list[str], crashes: list[str], strays: list[int]
    ) -> None:
        """Any mix of failures, crashes and repeated calls."""
        with pytest.MonkeyPatch.context() as patch:
            clock = Clock()
            patch.setattr(timezone, "now", clock)
            left = iter(crashes)
            for owner, name in CRASH_POINTS:
                patch.setattr(owner, name, crashing(getattr(owner, name), left))
            payments = [pay(scenario) for scenario in scenarios]
            for stray in strays:
                if stray < len(payments):
                    queue.add("process_payment", payments[stray].id, timedelta())
                    queue.add("process_payment", payments[stray].id, timedelta())
            clock.run_until_quiet()
        for payment in payments:
            payment.refresh_from_db()
            charges = Charge.objects.filter(reference=payment.id)
            assert charges.count() == 1
            declined = payment.scenario == Scenario.DECLINE
            assert payment.status == ("failed" if declined else "succeeded")
            kinds = list(payment.events.order_by("id").values_list("kind", flat=True))
            assert kinds == ["created", payment.status]
        paid = sum(p.amount for p in payments if p.status == "succeeded")
        totals = books.trial_balance()
        assert sum(totals.values()) == 0
        assert totals.get("provider", 0) == paid
        assert Entry.objects.count() == sum(p.status == "succeeded" for p in payments)

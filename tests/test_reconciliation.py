import uuid
from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.utils import timezone
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from ledger import books
from merchants import keys
from merchants.models import Merchant
from payments import processing
from payments import service as payments
from payments.models import Payment
from provider import simulator
from provider.models import Charge, Kind, Scenario
from reconciliation import matching, resolving
from reconciliation.models import Discrepancy, StatementLine
from refunds import processing as refund_processing
from refunds import service as refunds
from refunds.models import Refund
from tasks import queue

from .clock import Clock
from .conftest import Together


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    clock = Clock()
    monkeypatch.setattr(timezone, "now", clock)
    yield clock


@pytest.fixture
def shop(db: None) -> Merchant:
    return keys.create_merchant("Shop")


def pay(shop: Merchant, scenario: str = Scenario.NORMAL) -> Payment:
    payment = payments.create(shop, 10_000, "order", scenario)
    processing.start(payment)
    return payment


def give_back(payment: Payment, scenario: str) -> Refund:
    refund = refunds.create(payment.id, 4_000, scenario)
    refund_processing.start(refund)
    return refund


def totals() -> dict[str, int]:
    return {code.split(":")[0]: n for code, n in books.trial_balance().items()}


def in_statement() -> int:
    lines = StatementLine.objects.all()
    return sum(n.amount if n.kind == Kind.CHARGE else -n.amount for n in lines)


def check_books() -> None:
    """What the provider holds is what its statement says, but for what is missing."""
    missing = sum(
        -found.amount
        for found in Discrepancy.objects.filter(
            kind="missing", resolution=None
        ).exclude(action="matched")
    )
    assert totals().get("provider", 0) == in_statement() + missing
    assert sum(totals().values()) == 0


def go(clock: Clock) -> None:
    clock.run_until_quiet()
    clock.now += timedelta(hours=9)
    matching.run()
    clock.run_until_quiet()
    matching.run()
    check_books()


@pytest.mark.parametrize("scenario", sorted(Scenario.recoverable()))
def test_nothing_is_reported_when_all_went_well(
    shop: Merchant, clock: Clock, scenario: str
) -> None:
    pay(shop)
    payment = pay(shop, scenario)
    clock.run_until_quiet()
    payment.refresh_from_db()
    if payment.status == "succeeded":
        give_back(payment, scenario)
    go(clock)
    assert not Discrepancy.objects.exists()
    assert not StatementLine.objects.filter(matched_at=None).exists()


def test_a_silent_payment_is_settled_by_the_statement(
    shop: Merchant, clock: Clock
) -> None:
    payment = pay(shop, Scenario.SILENT)
    go(clock)
    payment.refresh_from_db()
    assert payment.status == "succeeded"
    assert not Discrepancy.objects.exists()


def test_money_taken_after_a_failure_goes_back_by_itself(
    shop: Merchant, clock: Clock
) -> None:
    payment = pay(shop, Scenario.LATE_SUCCESS)
    go(clock)
    payment.refresh_from_db()
    assert payment.status == "failed"
    found = Discrepancy.objects.get()
    assert (found.kind, found.status) == ("after_failure", "resolved")
    assert found.approved_by == "policy"
    assert Charge.objects.get(kind="return").amount == 10_000
    assert totals()["provider"] == totals()["suspense"] == 0


@pytest.mark.parametrize(
    ("scenario", "kind", "amount"),
    [
        (Scenario.WRONG_AMOUNT, "wrong_amount", 500),
        (Scenario.MOVED_TWICE, "unknown", 10_000),
    ],
)
def test_money_nobody_expected_waits_in_suspense(
    shop: Merchant, clock: Clock, scenario: str, kind: str, amount: int
) -> None:
    pay(shop, scenario)
    go(clock)
    found = Discrepancy.objects.get()
    assert (found.kind, found.amount, found.status) == (kind, amount, "open")
    assert totals()["suspense"] == -amount
    assert totals()["merchant"] == -9_800


def test_money_in_suspense_is_given_back(shop: Merchant, clock: Clock) -> None:
    pay(shop, Scenario.MOVED_TWICE)
    go(clock)
    found = Discrepancy.objects.get()
    resolving.propose(found.id, "return", "Ange", "Charged twice")
    resolving.approve(found.id, "Merveille")
    go(clock)
    found.refresh_from_db()
    assert found.status == "resolved"
    assert totals()["suspense"] == 0
    assert totals()["provider"] == 10_000
    assert Discrepancy.objects.count() == 1


def test_money_in_suspense_goes_to_the_merchant_when_it_is_known(
    shop: Merchant, clock: Clock
) -> None:
    pay(shop, Scenario.WRONG_AMOUNT)
    go(clock)
    found = Discrepancy.objects.get()
    resolving.propose(found.id, "credit", "Ange", "A tip")
    resolving.approve(found.id, "Merveille")
    assert totals()["merchant"] == -10_300
    assert totals()["suspense"] == 0
    go(clock)


def test_a_payment_not_in_the_statement_is_missing_only_after_a_while(
    shop: Merchant, clock: Clock
) -> None:
    pay(shop, Scenario.NOT_IN_STATEMENT)
    clock.run_until_quiet()
    matching.run()
    assert not Discrepancy.objects.exists()
    go(clock)
    found = Discrepancy.objects.get()
    assert (found.kind, found.amount) == ("missing", -10_000)
    resolving.propose(found.id, "write_off", "Ange", "Lost")
    resolving.approve(found.id, "Merveille")
    assert totals()["provider"] == 0
    assert totals()["differences"] == 10_000
    assert totals()["merchant"] == -9_800
    go(clock)


def test_a_missing_payment_that_appears_is_closed(shop: Merchant, clock: Clock) -> None:
    pay(shop, Scenario.NOT_IN_STATEMENT)
    go(clock)
    Charge.objects.update(scenario="normal")
    go(clock)
    found = Discrepancy.objects.get()
    assert (found.status, found.action) == ("resolved", "matched")


def test_one_that_appears_once_written_off_is_reported_again(
    shop: Merchant, clock: Clock
) -> None:
    pay(shop, Scenario.NOT_IN_STATEMENT)
    go(clock)
    found = Discrepancy.objects.get()
    resolving.propose(found.id, "write_off", "Ange", "Lost")
    resolving.approve(found.id, "Merveille")
    Charge.objects.update(scenario="normal")
    go(clock)
    late = Discrepancy.objects.get(kind="late")
    assert late.amount == 10_000
    resolving.propose(late.id, "write_off", "Ange", "Found")
    resolving.approve(late.id, "Merveille")
    assert totals()["differences"] == totals()["suspense"] == 0
    go(clock)


def test_a_payment_the_provider_never_got_is_stuck_then_failed(
    shop: Merchant, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def silent(*args: Any) -> None:
        raise simulator.NoAnswer

    payment = pay(shop)
    with monkeypatch.context() as patch:
        patch.setattr(simulator, "charge", silent)
        go(clock)
    found = Discrepancy.objects.get()
    assert (found.kind, found.status) == ("stuck", "open")
    resolving.propose(found.id, "fail", "Ange", "Never reached them")
    resolving.approve(found.id, "Merveille")
    payment.refresh_from_db()
    assert payment.status == "failed"


def test_a_stuck_payment_that_settles_is_closed(shop: Merchant, clock: Clock) -> None:
    payment = payments.create(shop, 100, "order")
    go(clock)
    processing.settle(payment.id, False)
    go(clock)
    found = Discrepancy.objects.get()
    assert (found.status, found.action) == ("resolved", "matched")


@pytest.mark.parametrize(
    ("scenario", "kind", "amount"),
    [
        (Scenario.SILENT, None, 0),
        (Scenario.LATE_SUCCESS, "after_failure", -4_000),
        (Scenario.WRONG_AMOUNT, "wrong_amount", -500),
        (Scenario.MOVED_TWICE, "unknown", -4_000),
        (Scenario.NOT_IN_STATEMENT, "missing", 4_000),
    ],
)
def test_the_faults_of_refunds_are_found_and_written_off(
    shop: Merchant, clock: Clock, scenario: str, kind: str | None, amount: int
) -> None:
    payment = pay(shop)
    clock.run_until_quiet()
    refund = give_back(payment, scenario)
    go(clock)
    refund.refresh_from_db()
    if kind is None:
        assert refund.status == "succeeded"
        assert not Discrepancy.objects.exists()
        return
    found = Discrepancy.objects.get()
    assert (found.kind, found.amount) == (kind, amount)
    assert resolving.allowed(found) == {"write_off"}
    resolving.propose(found.id, "write_off", "Ange", "Too small to chase")
    resolving.approve(found.id, "Merveille")
    assert totals().get("suspense", 0) == 0
    assert totals()["differences"] == -amount
    go(clock)


def test_two_people_are_needed(shop: Merchant, clock: Clock) -> None:
    pay(shop, Scenario.WRONG_AMOUNT)
    go(clock)
    found = Discrepancy.objects.get()
    for action, by, note in (
        ("fail", "Ange", "why"),
        ("credit", "", "why"),
        ("credit", "policy", "why"),
        ("credit", "Ange", ""),
    ):
        with pytest.raises(resolving.NotPossible):
            resolving.propose(found.id, action, by, note)
    with pytest.raises(resolving.NotPossible):
        resolving.approve(found.id, "Merveille")
    resolving.propose(found.id, "credit", "Ange", "A tip")
    for by in ("Ange", "", "policy"):
        with pytest.raises(resolving.NotPossible):
            resolving.approve(found.id, by)
    with pytest.raises(IntegrityError):
        Discrepancy.objects.update(approved_by="Ange")


def test_a_rejected_proposal_can_be_made_again(shop: Merchant, clock: Clock) -> None:
    pay(shop, Scenario.WRONG_AMOUNT)
    go(clock)
    found = Discrepancy.objects.get()
    resolving.propose(found.id, "credit", "Ange", "A tip")
    resolving.reject(found.id, "Merveille")
    resolving.propose(found.id, "write_off", "Merveille", "Small")
    resolving.approve(found.id, "Ange")
    with pytest.raises(resolving.NotPossible):
        resolving.approve(found.id, "Ange")


def test_approvals_at_the_same_time_move_the_money_once(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    payment = pay(shop, Scenario.WRONG_AMOUNT)
    payments.succeed(payment.id)
    simulator.charge(payment.id, payment.amount, payment.scenario)
    Charge.objects.update(decided_at=timezone.now() - timedelta(minutes=1))
    matching.run()
    found = Discrepancy.objects.get()
    resolving.propose(found.id, "credit", "Ange", "A tip")
    outcomes = together(6, lambda: resolving.approve(found.id, "Merveille").status)
    assert outcomes.count("resolved") == 1
    assert totals()["merchant"] == -10_300


def test_runs_at_the_same_time_report_once(together: Together) -> None:
    shop = keys.create_merchant("Shop")
    for scenario in (Scenario.WRONG_AMOUNT, Scenario.MOVED_TWICE):
        payment = pay(shop, scenario)
        payments.succeed(payment.id)
        simulator.charge(payment.id, payment.amount, payment.scenario)
    Charge.objects.update(decided_at=timezone.now() - timedelta(minutes=1))
    outcomes = together(6, matching.run)
    assert not [o for o in outcomes if isinstance(o, Exception)]
    assert Discrepancy.objects.count() == 2
    assert totals()["suspense"] == -10_500
    check_books()


def test_a_line_that_cannot_be_matched_does_not_stop_the_others(
    shop: Merchant, clock: Clock
) -> None:
    for kind in (Kind.RETURN, Kind.CHARGE):
        StatementLine.objects.create(
            reference=uuid.uuid4(),
            kind=kind,
            amount=5,
            moved_at=clock.now,
        )
    matching.run()
    assert Discrepancy.objects.get().kind == "unknown"
    assert StatementLine.objects.get(matched_at=None).kind == Kind.RETURN


def test_the_commands_list_and_resolve(shop: Merchant, clock: Clock) -> None:
    pay(shop, Scenario.WRONG_AMOUNT)
    clock.run_until_quiet()
    out = StringIO()
    call_command("reconcile", stdout=out)
    assert "1 waiting" in out.getvalue()
    call_command("discrepancies", stdout=out)
    found = Discrepancy.objects.get()
    assert (
        f"{found.id} wrong_amount +500 open credit/return/write_off" in out.getvalue()
    )
    who = ["--by", "Ange"]
    call_command(
        "discrepancies", "propose", str(found.id), "credit", *who, "--note", "x"
    )
    with pytest.raises(CommandError, match="Someone else"):
        call_command("discrepancies", "approve", str(found.id), *who)
    call_command("discrepancies", "approve", str(found.id), "--by", "Merveille")
    found.refresh_from_db()
    assert found.status == "resolved"


class AnyDay(TestCase):
    @given(
        day=st.lists(
            st.tuples(
                st.sampled_from(Scenario.values),
                st.sampled_from([None, *Scenario.values]),
            ),
            min_size=1,
            max_size=5,
        ),
        choices=st.lists(
            st.integers(min_value=0, max_value=5), min_size=10, max_size=10
        ),
    )
    @settings(max_examples=60, deadline=None)
    def test_ends_with_books_that_match_the_statement(
        self, day: list[tuple[str, str | None]], choices: list[int]
    ) -> None:
        """Any mix of payments, refunds and faults, resolved in any way."""
        with pytest.MonkeyPatch.context() as patch:
            clock = Clock()
            patch.setattr(timezone, "now", clock)
            shop = keys.create_merchant("Shop")
            made = [pay(shop, scenario) for scenario, _ in day]
            clock.run_until_quiet()
            for payment, (_, scenario) in zip(made, day, strict=True):
                payment.refresh_from_db()
                if scenario and payment.status == "succeeded":
                    give_back(payment, scenario)
            go(clock)
            left = iter(choices * 10)
            for found in Discrepancy.objects.filter(status="open"):
                actions = sorted(resolving.allowed(found))
                action = actions[next(left) % len(actions)]
                resolving.propose(found.id, action, "Ange", "x")
                resolving.approve(found.id, "Merveille")
            go(clock)
            assert queue.run_due() == 0
        assert not Discrepancy.objects.exclude(status="resolved").exists()
        assert totals().get("suspense", 0) == 0
        assert totals().get("refunds", 0) == 0
        assert totals().get("provider", 0) == in_statement()

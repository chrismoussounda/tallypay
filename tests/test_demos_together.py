import itertools
import time

import pytest
from django.db import transaction

from demos import opening
from merchants import demos
from merchants.models import Demo
from payments import service as payments
from refunds import service as refunds

from .conftest import Together


def test_opened_at_the_same_time_no_more_than_allowed(
    together: Together, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opening, "SAMPLE", [])
    outcomes = together(6, lambda: opening.open_one("a"))
    assert sum(isinstance(o, opening.TooMany) for o in outcomes) == 3
    assert Demo.objects.count() == 3


def test_at_the_same_time_no_more_than_allowed(
    together: Together, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Demo, "MOST", 2)
    monkeypatch.setattr(opening, "SAMPLE", [])
    merchant = opening.open_one("a").merchant
    outcomes = together(6, lambda: payments.create(merchant, 1, "x"))
    assert sum(isinstance(o, demos.UsedUp) for o in outcomes) == 4
    assert merchant.payments.count() == 2


def test_a_payment_and_a_refund_at_the_same_time_do_not_block_each_other(
    together: Together,
) -> None:
    merchant = opening.open_one("a").merchant
    paid = merchant.payments.get(reference="CMD-1001")
    turns = itertools.cycle([True, False])

    def act() -> None:
        with transaction.atomic():
            if next(turns):
                payments.create(merchant, 1, "x")
                # So that the refund has asked for its locks meanwhile.
                time.sleep(0.5)
            else:
                time.sleep(0.2)
                refunds.create(paid.id, 100)

    assert together(2, act) == [None, None]

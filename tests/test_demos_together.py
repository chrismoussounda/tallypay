import pytest

from demos import opening
from merchants import demos
from merchants.models import Demo
from payments import service as payments

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

import time

import pytest
from django.test import Client

from api import core
from limits.service import Limit, TooMany
from merchants import keys

from .conftest import Together


@pytest.mark.django_db
def test_a_burst_then_a_wait_then_again() -> None:
    limit = Limit(per_second=10, burst=3)
    for _ in range(3):
        limit.take("a")
    with pytest.raises(TooMany) as refused:
        limit.take("a")
    assert refused.value.wait == 1
    limit.take("b")
    time.sleep(0.15)
    limit.take("a")


@pytest.mark.django_db
def test_the_wait_is_long_enough() -> None:
    limit = Limit(per_second=1 / 60, burst=1)
    limit.take("a")
    for _ in range(2):
        with pytest.raises(TooMany) as refused:
            limit.take("a")
        # A refusal does not make it longer.
        assert 58 <= refused.value.wait <= 60


def test_at_the_same_time_no_more_than_the_burst(together: Together) -> None:
    limit = Limit(per_second=1 / 60, burst=4)
    outcomes = together(10, lambda: limit.take("a"))
    assert outcomes.count(None) == 4
    assert sum(isinstance(o, TooMany) for o in outcomes) == 6


@pytest.mark.django_db
def test_the_api_says_when_to_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "LIMIT", Limit(per_second=1 / 60, burst=2))
    shop, other = keys.create_merchant("Shop"), keys.create_merchant("Other")
    first, second, theirs = keys.issue(shop), keys.issue(shop), keys.issue(other)

    def ask(key: str) -> int:
        answer = Client().get(
            "/v1/payments", headers={"Authorization": f"Bearer {key}"}
        )
        if answer.status_code == 429:
            assert answer.json()["code"] == "too_many_requests"
            assert 58 <= int(answer["Retry-After"]) <= 60
        return int(answer.status_code)

    # Two keys of one merchant share its limit.
    assert [ask(first), ask(second), ask(first), ask(second)] == [200, 200, 429, 429]
    assert ask(theirs) == 200
    assert ask("wrong") == 401

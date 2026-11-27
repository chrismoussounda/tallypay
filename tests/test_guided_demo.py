import uuid
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from dashboard import guide
from merchants.models import Demo
from payments import service as payments
from provider.models import Charge
from reconciliation.models import Discrepancy
from webhooks import service as webhooks
from webhooks.models import Delivery

from .test_demos import become, visit

pytestmark = pytest.mark.django_db


# The guide


def test_a_new_visitor_is_shown_step_one_with_a_way_to_do_it() -> None:
    client = visit()
    page = client.get("/app/overview").content.decode()
    assert "Step 1 of 4" in page
    assert 'href="/app/payments/new?scenario=wrong_amount"' in page
    assert "nobody can move money alone" in page


def test_the_step_button_changes_role_when_it_must() -> None:
    client = visit("proposer")
    page = client.get("/app/discrepancies").content.decode()
    # Step 1 is the shop's: a form that becomes the shop and opens the page.
    assert 'name="who" value="owner"' in page
    assert 'name="next" value="/app/payments/new?scenario=wrong_amount"' in page


def test_becoming_someone_can_open_the_page_of_the_step() -> None:
    client = visit()
    answer = client.post(
        "/app/demo/become", {"who": "proposer", "next": "/app/discrepancies"}
    )
    assert answer["Location"] == "/app/discrepancies"


@pytest.mark.parametrize(
    "going",
    [
        "https://elsewhere.example/app/",
        "//elsewhere.example/app/",
        "/admin/",
        "/app/../admin/",
    ],
)
def test_becoming_someone_never_leaves_the_app(going: str) -> None:
    client = visit()
    answer = client.post("/app/demo/become", {"who": "proposer", "next": going})
    assert answer["Location"] == "/app/"


def test_the_last_step_is_operator_twos_once_a_fix_is_proposed() -> None:
    client = visit()
    demo = Demo.objects.get()
    payment = payments.create(demo.merchant, 5_000, "x", "wrong_amount")
    Charge.objects.create(
        reference=payment.id,
        amount=5_500,
        scenario="wrong_amount",
        decided_at=timezone.now(),
    )
    payments.succeed(payment.id)
    webhooks._record(
        Delivery.objects.get(endpoint__merchant=demo.merchant).id, 1, 204, ""
    )
    become(client, "proposer")
    client.post("/app/demo/reconcile")
    current: Any = guide.steps(demo)["current"]
    assert (current["number"], current["who"]) == (4, "proposer")
    found = Discrepancy.objects.get(reference=payment.id)
    client.post(
        f"/app/discrepancies/{found.id}/propose", {"action": "credit", "note": "x"}
    )
    current = guide.steps(demo)["current"]
    assert (current["number"], current["who"]) == (4, "approver")
    assert (
        'name="who" value="approver"'
        in client.get("/app/discrepancies").content.decode()
    )


# The first step's form


def test_the_scenario_of_the_first_step_is_picked_and_shown() -> None:
    client = visit()
    page = client.get("/app/payments/new?scenario=wrong_amount").content.decode()
    assert '<details class="more" open>' in page
    radio = page.split('value="wrong_amount"', 1)[1].split(">", 1)[0]
    assert "checked" in radio


def test_an_unknown_scenario_is_ignored() -> None:
    client = visit()
    page = client.get("/app/payments/new?scenario=steal").content.decode()
    assert '<details class="more">' in page


# Your own payments


def test_the_visitors_own_payments_are_marked() -> None:
    client = visit()
    assert 'class="yours"' not in client.get("/app/payments").content.decode()
    client.post(
        "/app/payments/new",
        {
            "once": uuid.uuid4(),
            "amount": 100,
            "reference": "mine",
            "scenario": "normal",
        },
    )
    assert 'class="yours"' in client.get("/app/payments").content.decode()


def test_the_home_page_has_one_way_in() -> None:
    page = Client().get("/").content.decode()
    assert "Start the 2-minute tour" in page
    assert "Run the shop" not in page


def test_a_payment_that_cannot_go_wrong_does_not_count_for_step_one() -> None:
    client = visit()
    client.post(
        "/app/payments/new",
        {"once": uuid.uuid4(), "amount": 100, "reference": "x", "scenario": "normal"},
    )
    current: Any = guide.steps(Demo.objects.get())["current"]
    assert current["number"] == 1

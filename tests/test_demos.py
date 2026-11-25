import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import Action, User
from dashboard import core, guide
from demos import opening
from ledger import books
from limits.service import Limit
from merchants import demos, keys
from merchants.models import ApiKey, Demo, DemoDay
from payments import service as payments
from payments.models import Payment
from provider.models import Charge
from reconciliation import matching, resolving
from reconciliation.models import Discrepancy, StatementLine
from refunds import service as refunds
from tasks import queue
from tasks.models import Task
from webhooks import sender, signing
from webhooks import service as webhooks
from webhooks.models import Delivery, Endpoint

from .receiver import Receiver
from .test_dashboard import Shop, ago, browser, operator

pytestmark = pytest.mark.django_db


def visit(who: str = "owner", client: Client | None = None, **more: Any) -> Client:
    client = client or Client()
    response = client.post("/demo/open", {"who": who}, **more)
    assert response["Location"] == "/app/", response
    return client


def user_of(client: Client) -> User:
    return User.objects.get(id=client.session["_auth_user_id"])


def become(client: Client, who: str) -> None:
    assert client.post("/app/demo/become", {"who": who})["Location"] == "/app/"


def new_payment(client: Client) -> Any:
    form = {"once": uuid.uuid4(), "amount": 100, "reference": "x", "scenario": "normal"}
    return client.post("/app/payments/new", form)


# Opening


def test_each_visitor_gets_a_shop_of_their_own() -> None:
    first, second = visit(), visit(REMOTE_ADDR="10.0.0.2")
    assert Demo.objects.count() == 2
    mine, theirs = user_of(first), user_of(second)
    assert mine.merchant_id != theirs.merchant_id
    assert first.get("/app/")["Location"] == "/app/overview"
    for payment in Payment.objects.filter(merchant=theirs.merchant):
        assert first.get(f"/app/payments/{payment.id}").status_code == 404
    assert not Action.objects.exists()


def test_opening_again_goes_back_to_the_same_one() -> None:
    client = visit()
    visit("proposer", client)
    assert Demo.objects.count() == 1
    assert user_of(client).is_operator


def test_a_link_opens_nothing() -> None:
    assert Client().get("/demo/open").status_code == 405
    assert Client(enforce_csrf_checks=True).post("/demo/open").status_code == 403
    assert Client().post("/demo/open", {"who": "approver"}).status_code == 400
    assert not Demo.objects.exists()


def test_the_sample_is_in_the_books_as_the_provider_says() -> None:
    demo = opening.open_one("a")
    assert sum(books.trial_balance().values()) == 0
    moved = sum(
        c.amount
        for c in Charge.objects.exclude(scenario__in=["decline", "not_in_statement"])
    )
    # Only the payment that is not in the statement is in our books alone.
    assert books.trial_balance()["provider"] == moved + 18_500
    found = Discrepancy.objects.values_list("kind", flat=True)
    assert sorted(found) == ["missing", "wrong_amount"]
    # The provider never said so, but its statement did.
    silent = demo.merchant.payments.get(scenario="silent")
    assert silent.status == "succeeded"
    # Nothing of it wakes the app.
    assert [t.kind for t in Task.objects.all()] == ["close_demo"]
    assert not Delivery.objects.exists()


def test_a_daily_run_finds_nothing_more_in_a_sample() -> None:
    opening.open_one("a")
    with pytest.MonkeyPatch.context() as patch:
        then = timezone.now() + timedelta(days=2)
        patch.setattr(timezone, "now", lambda: then)
        matching.run()
    assert Discrepancy.objects.count() == 2


# How many


def test_only_some_demos_for_each_address() -> None:
    for _ in range(opening.PER_ADDRESS):
        opening.open_one("a")
    with pytest.raises(opening.TooMany):
        opening.open_one("a")
    opening.open_one("b")
    Demo.objects.update(created_at=timezone.now() - timedelta(hours=1, seconds=1))
    opening.open_one("a")


def test_only_some_demos_each_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opening, "PER_DAY", 2)
    for address in "ab":
        opening.open_one(address)
    with pytest.raises(opening.Full):
        opening.open_one("c")
    Demo.objects.update(created_at=timezone.now() - timedelta(days=1, seconds=1))
    opening.open_one("c")


def test_a_refusal_is_said_on_the_home_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opening, "PER_ADDRESS", 0)
    page = Client().post("/demo/open", {"who": "owner"}, follow=True)
    assert "Try again in an hour." in page.content.decode()
    monkeypatch.setattr(opening, "PER_DAY", 0)
    monkeypatch.setattr(opening, "PER_ADDRESS", 1)
    page = Client().post("/demo/open", {"who": "owner"}, follow=True)
    assert "Try tomorrow." in page.content.decode()


def test_the_address_is_kept_only_hashed_and_while_open() -> None:
    demo = opening.open_one("10.1.2.3")
    assert len(demo.address) == 64
    opening._close(demo.merchant_id, 1)
    demo.refresh_from_db()
    assert demo.address == ""


# What it may do


def test_payments_refunds_and_replays_are_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = visit()
    demo = Demo.objects.get()
    assert new_payment(client).status_code == 302
    payment = demo.merchant.payments.get(reference="CMD-1001")
    refunds.create(payment.id, 100)
    delivery = Delivery.objects.get()
    Delivery.objects.update(status="failed")
    webhooks.replay(delivery.id)
    demo.refresh_from_db()
    assert demo.used == 3
    Delivery.objects.update(status="failed")
    monkeypatch.setattr(Demo, "MOST", 3)
    page = new_payment(client)
    assert page.status_code == 422
    assert "used up" in page.content.decode()
    page = client.post(
        f"/app/payments/{payment.id}/refund",
        {
            "once": uuid.uuid4(),
            "amount": 1,
            "reason": "duplicate",
            "scenario": "normal",
        },
    )
    assert "used up" in page.content.decode()
    page = client.post(f"/app/deliveries/{delivery.id}")
    assert "used up" in page.content.decode()
    key = keys.issue(demo.merchant)
    answer = Client().post(
        "/v1/payments",
        {"amount": 1, "reference": "x"},
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": "k"},
    )
    assert (answer.status_code, answer.json()["code"]) == (403, "demo_used_up")
    answer = Client().post(
        f"/v1/webhook-deliveries/{delivery.id}/replay",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert answer.status_code == 403
    assert demo.merchant.payments.count() == len(opening.SAMPLE) + 1


def test_all_the_demos_of_a_day_are_counted_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demos, "PER_DAY", 2)
    first, second = opening.open_one("a").merchant, opening.open_one("b").merchant
    payments.create(first, 1, "x")
    payments.create(second, 1, "x")
    with pytest.raises(demos.UsedUp):
        payments.create(first, 1, "x")
    payments.create(Shop().merchant, 1, "x")


def test_forms_sent_too_fast_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = visit()
    monkeypatch.setattr(core, "DEMO_LIMIT", Limit(per_second=0.01, burst=2))
    codes = [client.post("/app/demo/reconcile").status_code for _ in range(3)]
    assert codes == [302, 302, 429]
    assert client.get("/app/overview").status_code == 200
    page = client.post("/app/demo/reconcile").content.decode()
    assert "Wait a few minutes" in page


# Webhooks


def test_webhooks_go_nowhere_else() -> None:
    merchant = opening.open_one("a").merchant
    with pytest.raises(sender.Refused):
        webhooks.add_endpoint(merchant, "https://example.com/x")
    assert merchant.endpoints.count() == 1


def test_the_test_server_gets_a_real_signed_webhook() -> None:
    receiver = Receiver()
    with receiver.running(), override_settings(PUBLIC_URL=receiver.url[:-12]):
        demo = opening.open_one("a")
        payment = payments.create(demo.merchant, 100, "x")
        payments.succeed(payment.id)
        queue.run_due()
    headers, body = receiver.got[0]
    path = f"/demo/inbox/{demo.merchant_id}"

    def send(content: bytes = body, changed: dict[str, str] | None = None) -> int:
        sent = {k: v for k, v in headers.items() if k.startswith("webhook-")}
        sent |= changed or {}
        response = Client(enforce_csrf_checks=True).post(
            path, content, content_type="application/json", headers=sent
        )
        return int(response.status_code)

    assert send() == 204
    assert send(body + b" ") == 401
    assert send(changed={"webhook-signature": "v1,x"}) == 401
    assert send(changed={"webhook-id": "x"}) == 401
    old = int(headers["webhook-timestamp"]) - 301
    secret = signing.secret_of(Endpoint.objects.get().id)
    signed = signing.headers(secret, uuid.UUID(headers["webhook-id"]), old, body)
    assert send(changed={k.lower(): v for k, v in signed.items()}) == 401
    Demo.objects.update(inbox_works=False)
    assert send() == 503
    Demo.objects.update(inbox_works=True, closed_at=timezone.now())
    assert send() == 404


def test_the_visitor_can_switch_the_test_server_off_and_on() -> None:
    client = visit()
    client.post("/app/demo/inbox", {"works": "0"})
    assert not Demo.objects.get().inbox_works
    client.post("/app/demo/inbox", {"works": "1"})
    assert Demo.objects.get().inbox_works


# Operators


def test_one_proposes_then_the_other_approves_without_passwords() -> None:
    client = visit("proposer")
    found = Discrepancy.objects.get(kind="wrong_amount")
    path = f"/app/discrepancies/{found.id}"
    client.post(f"{path}/propose", {"action": "credit", "note": "500 too much"})
    assert client.post(f"{path}/approve", {"proposal": 1}).status_code == 422
    become(client, "approver")
    assert 'type="password"' not in client.get(f"{path}/approve").content.decode()
    assert client.post(f"{path}/approve", {"proposal": 1}).status_code == 302
    found.refresh_from_db()
    assert found.status == "resolved"
    assert sum(books.trial_balance().values()) == 0


def test_an_operator_of_a_demo_sees_nothing_else() -> None:
    client = visit("proposer")
    other = opening.open_one("b")
    shop = Shop("Secret")
    mine = set(resolving.seen_by(user_of(client)).values_list("id", flat=True))
    assert len(mine) == 2
    page = client.get("/app/discrepancies").content.decode()
    for found in Discrepancy.objects.exclude(id__in=mine):
        assert str(found.id) not in page
        path = f"/app/discrepancies/{found.id}"
        for end in ("", "/propose", "/approve", "/reject"):
            assert client.get(path + end).status_code == 404
            assert client.post(path + end).status_code in (404, 405)
        with pytest.raises(resolving.NotPossible):
            resolving.propose(found.id, "write_off", user_of(client), "x")
    assert Discrepancy.objects.count() == 4
    real = operator()
    assert resolving.seen_by(real).count() == 0
    for found in Discrepancy.objects.all():
        with pytest.raises(resolving.NotPossible):
            resolving.propose(found.id, "write_off", real, "x")
    assert matching.run() == 0
    books_page = client.get("/app/books").content.decode()
    assert shop.merchant.name not in books_page
    assert "The books balance" in books_page
    assert books_page.count("Boutique Démo") == 1
    assert other.merchant.id != user_of(client).demo_id


def test_the_visitor_compares_only_their_own_money() -> None:
    client = visit("proposer")
    shop = Shop()
    merchant = Demo.objects.get().merchant
    for owner in (merchant, shop.merchant):
        payment = payments.create(owner, 5_000, "x", "wrong_amount")
        Charge.objects.create(
            reference=payment.id,
            amount=5_500,
            scenario="wrong_amount",
            decided_at=timezone.now(),
        )
        payments.succeed(payment.id)
    absent = payments.create(merchant, 700, "x", "not_in_statement")
    payments.succeed(absent.id)
    assert client.post("/app/demo/reconcile").status_code == 302
    theirs = shop.merchant.payments.values("id")
    assert not StatementLine.objects.filter(reference__in=theirs).exists()
    found = resolving.seen_by(user_of(client))
    assert found.count() == 4
    assert Discrepancy.objects.count() == 4


def test_nobody_becomes_someone_of_another_demo() -> None:
    client = visit()
    other = opening.open_one("b")
    become(client, "approver")
    assert user_of(client).demo_id != other.merchant_id
    for who in ("", "nobody", "owner-" + other.merchant_id.hex):
        answer = client.post("/app/demo/become", {"who": who})
        if answer.status_code == 302:
            assert user_of(client).demo_id != other.merchant_id
        else:
            assert answer.status_code == 404
    assert Client().post("/app/demo/become", {"who": "owner"}).status_code == 302
    real = Client()
    real.force_login(operator())
    assert real.post("/app/demo/become", {"who": "owner"}).status_code in (302, 403)
    assert not user_of(real).demo_id if "_auth_user_id" in real.session else True


def test_the_visitor_stays_in_until_the_demo_closes() -> None:
    client = visit()
    demo = Demo.objects.get()
    become(client, "approver")
    left = client.session.get_expiry_date() - demo.closes_at
    assert abs(left) < timedelta(seconds=1)
    assert not client.session.get_expire_at_browser_close()
    Demo.objects.update(created_at=timezone.now() - timedelta(hours=23, minutes=59))
    assert client.get("/app/discrepancies").status_code == 200
    client.post("/app/sign-out")
    assert client.get("/app/discrepancies").status_code == 302


def test_past_its_time_it_is_closed_even_if_the_task_is_late() -> None:
    client = visit()
    demo = Demo.objects.get()
    key = keys.issue(demo.merchant)
    inbox = f"/demo/inbox/{demo.merchant_id}"
    bearer = {"Authorization": f"Bearer {key}"}
    assert Client().get("/v1/payments", headers=bearer).status_code == 200
    assert Client().post(inbox).status_code == 401
    Demo.objects.update(created_at=timezone.now() - Demo.LASTS)
    assert Demo.objects.get().closed_at is None
    assert client.get("/app/overview").status_code == 302
    assert Client().get("/v1/payments", headers=bearer).status_code == 401
    assert Client().post(inbox).status_code == 404
    with pytest.raises(demos.UsedUp):
        payments.create(demo.merchant, 1, "x")


def test_money_moved_twice_is_not_in_a_demo() -> None:
    client = visit()
    demo = Demo.objects.get()
    payment = demo.merchant.payments.get(reference="CMD-1001")
    for path in ("/app/payments/new", f"/app/payments/{payment.id}/refund"):
        page = client.get(path).content.decode()
        assert "moved_twice" not in page
        assert "wrong_amount" in page
    key = keys.issue(demo.merchant)
    for path, more in (
        ("/v1/payments", {"reference": "x"}),
        (f"/v1/payments/{payment.id}/refunds", {}),
    ):
        answer = Client().post(
            path,
            {"amount": 1, "scenario": "moved_twice", **more},
            content_type="application/json",
            headers={"Authorization": f"Bearer {key}", "Idempotency-Key": path},
        )
        assert (answer.status_code, answer.json()["code"]) == (422, "not_in_demo")
    form = {"once": uuid.uuid4(), "amount": 100, "reference": "x"}
    form["scenario"] = "moved_twice"
    assert client.post("/app/payments/new", form).status_code == 422
    demo.refresh_from_db()
    assert demo.used == 0
    shop = Shop()
    payments.create(shop.merchant, 1, "x", "moved_twice")
    page = browser(shop.user("owner")).get("/app/payments/new").content.decode()
    assert "moved_twice" in page


def test_money_missing_after_a_return_is_not_shown_to_real_operators() -> None:
    opening.open_one("a")
    found = Discrepancy.objects.get(kind="wrong_amount")
    Discrepancy.objects.create(
        kind="missing", reference=found.id, operation="return", amount=500
    )
    assert not resolving.seen_by(operator()).exists()


def test_the_books_show_real_operators_one_row_for_all_demos() -> None:
    opening.open_one("a")
    opening.open_one("b")
    shop = Shop("Realone")
    page = browser(operator()).get("/app/books").content.decode()
    assert shop.merchant.name in page
    assert "Boutique Démo" not in page
    assert page.count("All demos") == 1


def test_someone_signed_in_is_not_taken_out_of_their_account() -> None:
    real = operator()
    client = browser(real)
    page = client.post("/demo/open", {"who": "owner"}, follow=True)
    assert "Sign out to try" in page.content.decode()
    assert user_of(client) == real
    assert not Demo.objects.exists()


def test_what_happened_to_a_sample_is_told_in_order() -> None:
    client = visit()
    payment = Payment.objects.get(reference="CMD-1001")
    page = client.get(f"/app/payments/{payment.id}").content.decode()
    page = page[page.index("What happened") :]
    assert page.index("Created") < page.index("Succeeded")


def test_a_hidden_fault_is_hinted_in_a_demo_only() -> None:
    client = visit()
    merchant = Demo.objects.get().merchant
    hints = {}
    for payment in merchant.payments.all():
        page = client.get(f"/app/payments/{payment.id}").content.decode()
        hints[payment.scenario] = "become operator 1 and compare" in page
    assert {s for s, hinted in hints.items() if hinted} == {
        "silent",
        "wrong_amount",
        "not_in_statement",
    }
    shop = Shop()
    payment = payments.create(shop.merchant, 1, "x", "wrong_amount")
    payments.succeed(payment.id)
    answer = browser(shop.user("owner")).get(f"/app/payments/{payment.id}")
    assert "become operator 1" not in answer.content.decode()


def test_the_guide_ticks_what_was_done() -> None:
    client = visit()
    demo = Demo.objects.get()

    def did() -> list[bool]:
        return [step["did"] for step in guide.steps(demo)["steps"]]

    other = opening.open_one("b").merchant
    payments.succeed(payments.create(other, 1, "x").id)
    Delivery.objects.update(status="delivered")
    missing = resolving.seen_by(user_of(client)).get(kind="missing")
    # As the rules do, without anyone.
    resolving.close(missing, Discrepancy.Action.MATCHED)
    assert did() == [False] * 4
    payment = payments.create(demo.merchant, 5_000, "x", "wrong_amount")
    Charge.objects.create(
        reference=payment.id,
        amount=5_500,
        scenario="wrong_amount",
        decided_at=timezone.now(),
    )
    payments.succeed(payment.id)
    assert did() == [True, False, False, False]
    mine = Delivery.objects.get(endpoint__merchant=demo.merchant)
    webhooks._record(mine.id, 1, 204, "")
    assert did() == [True, True, False, False]
    become(client, "proposer")
    client.post("/app/demo/reconcile")
    assert did() == [True, True, True, False]
    found = Discrepancy.objects.get(reference=payment.id)
    path = f"/app/discrepancies/{found.id}"
    client.post(f"{path}/propose", {"action": "credit", "note": "x"})
    assert did()[3] is False
    become(client, "approver")
    client.post(f"{path}/approve", {"proposal": 1})
    assert did() == [True] * 4
    assert "You have seen it all." in client.get("/app/books").content.decode()


def test_the_books_and_the_statement_are_shown_side_by_side() -> None:
    client = visit("proposer")
    rows = client.get("/app/discrepancies").context["discrepancies"]
    shown = {row.paid.reference: (row.ours, row.theirs, row.amount) for row in rows}
    assert shown == {
        "CMD-1008": (20_000, 20_500, 500),
        "CMD-1010": (18_500, None, -18_500),
    }
    for row in rows:
        page = client.get(f"/app/discrepancies/{row.id}").content.decode()
        assert row.paid.reference in page


def test_crafted_input_is_refused() -> None:
    client = visit()
    assert client.post("/app/demo/become", {"who": "owner\x00"}).status_code == 404
    sent = {"webhook-id": str(uuid.uuid4()), "webhook-timestamp": "9" * 400}
    path = f"/demo/inbox/{Demo.objects.get().merchant_id}"
    assert Client().post(path, headers=sent).status_code == 401


def test_webhooks_of_a_demo_are_tried_again_three_times() -> None:
    demo = opening.open_one("a")
    payments.succeed(payments.create(demo.merchant, 100, "x").id)
    delivery = Delivery.objects.get()
    for attempt in range(1, 5):
        assert delivery.status == "pending"
        webhooks._record(delivery.id, attempt, 503, "")
        delivery.refresh_from_db()
    assert delivery.status == "failed"


def test_the_day_is_counted_whenever_the_demo_was_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demos, "PER_DAY", 1)
    first, second = opening.open_one("a"), opening.open_one("b")
    Demo.objects.filter(merchant=first.merchant).update(
        created_at=timezone.now() - timedelta(hours=23)
    )
    payments.create(first.merchant, 1, "x")
    Demo.objects.filter(merchant=first.merchant).update(
        created_at=timezone.now() - timedelta(hours=30)
    )
    with pytest.raises(demos.UsedUp):
        payments.create(second.merchant, 1, "x")
    DemoDay.objects.update(day=timezone.now().date() - timedelta(days=1))
    payments.create(second.merchant, 1, "x")


def test_nobody_in_a_demo_can_sign_in_with_a_password() -> None:
    demo = opening.open_one("a")
    for person in demo.people.all():
        for password in ("", "!", person.password):
            answer = Client().post(
                "/app/sign-in", {"email": person.email, "password": password}
            )
            assert answer.status_code == 422


def test_a_key_is_made_without_a_password() -> None:
    client = visit()
    page = client.post("/app/keys/new", {"name": "Website"}).content.decode()
    assert "tp_test_" in page
    endpoint = Endpoint.objects.get()
    shown = client.post(f"/app/webhooks/{endpoint.id}/secret", {"a": "b"})
    assert "whsec_" in shown.content.decode()


# Closing


def test_a_day_later_it_is_closed_and_the_books_stay() -> None:
    with ago(Demo.LASTS + timedelta(minutes=1)):
        client = visit()
        demo = Demo.objects.get()
        client.post("/app/keys/new", {"name": "Website"})
    before = books.trial_balance()
    # The task is found by the daily run, if QStash missed it.
    assert queue.run_due() == 1
    demo.refresh_from_db()
    assert demo.closed_at
    assert client.get("/app/overview").status_code == 302
    assert not ApiKey.objects.filter(revoked_at=None).exists()
    assert not Endpoint.objects.filter(removed_at=None).exists()
    assert not User.objects.filter(is_active=True).exists()
    assert books.trial_balance() == before
    with pytest.raises(demos.UsedUp):
        payments.create(demo.merchant, 1, "x")
    closed = demo.closed_at
    opening._close(demo.merchant_id, 2)
    demo.refresh_from_db()
    assert demo.closed_at == closed


def test_closing_one_leaves_the_others_open() -> None:
    first, second = opening.open_one("a"), opening.open_one("b")
    keys.issue(second.merchant)
    opening._close(first.merchant_id, 1)
    assert second.people.filter(is_active=True).count() == 3
    assert ApiKey.objects.filter(revoked_at=None).count() == 1
    assert second.merchant.endpoints.filter(removed_at=None).count() == 1

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.test import Client
from django.urls import URLPattern, get_resolver
from django.utils import timezone

from accounts.middleware import MAX_SESSION, STARTED
from accounts.models import Action, Attempt, User
from ledger import books
from ledger.models import Account
from merchants import keys
from merchants.models import ApiKey, Merchant
from payments import service as payments
from payments.models import Payment
from reconciliation.models import Discrepancy, StatementLine
from refunds import service as refunds
from refunds.models import Refund
from webhooks import service as webhooks
from webhooks import signing
from webhooks.models import Delivery, Endpoint

pytestmark = pytest.mark.django_db
PASSWORD = "correct horse battery staple"
HTMX = {"HX-Request": "true"}


class Shop:
    def __init__(self, name: str = "Shop") -> None:
        self.merchant = keys.create_merchant(name)
        self.name = name.lower()
        self.payment = self.pay(10_000, "order-1")
        self.endpoint = webhooks.add_endpoint(
            self.merchant, f"http://localhost/{self.name}"
        )
        keys.issue(self.merchant, "Website")
        self.key = ApiKey.objects.get(merchant=self.merchant)
        self.refund = refunds.create(self.payment.id, 1_000)
        self.delivery = Delivery.objects.get(endpoint=self.endpoint)

    def pay(self, amount: int, reference: str) -> Payment:
        return payments.succeed(payments.create(self.merchant, amount, reference).id)

    def user(self, role: str) -> User:
        return person(role, self.merchant, f"{role}@{self.name}.test")


def person(role: str, merchant: Merchant | None, email: str) -> User:
    found = User.objects.filter(email=email).first()
    return found or User.objects.create_user(
        email, PASSWORD, name=f"Ms {role}", role=role, merchant=merchant
    )


@contextmanager
def ago(delay: timedelta) -> Iterator[None]:
    then = timezone.now() - delay
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(timezone, "now", lambda: then)
        yield


def operator(name: str = "ange") -> User:
    return person("operator", None, f"{name}@tallypay.test")


def browser(user: User, htmx: bool = False) -> Client:
    client = Client(headers=HTMX if htmx else {})
    client.force_login(user)
    session = client.session
    session[STARTED] = time.time()
    session.save()
    return client


@pytest.fixture
def shop(db: None) -> Shop:
    return Shop()


def discrepancy(amount: int = 500) -> Discrepancy:
    provider = books.system("provider", Account.Kind.ASSET)
    suspense = books.system("suspense", Account.Kind.LIABILITY)
    return Discrepancy.objects.create(
        kind="unknown",
        reference=uuid.uuid4(),
        operation="charge",
        amount=amount,
        booking=books.post("x", {provider: amount, suspense: -amount}),
    )


def paths(shop: Shop, found: Discrepancy) -> dict[str, str]:
    """Every page of the dashboard, so that a new one cannot be forgotten."""
    ids = {
        "payment_id": shop.payment.id,
        "endpoint_id": shop.endpoint.id,
        "delivery_id": shop.delivery.id,
        "key_id": shop.key.id,
        "found_id": found.id,
    }
    resolver = next(p for p in get_resolver().url_patterns if str(p.pattern) == "app/")
    made = {}
    for route in resolver.url_patterns:  # type: ignore[union-attr]
        path = str(route.pattern)
        for name, value in ids.items():
            path = path.replace(f"<uuid:{name}>", str(value))
            path = path.replace(f"<int:{name}>", str(value))
        assert "<" not in path
        assert isinstance(route, URLPattern) and route.name
        made[route.name] = f"/app/{path}"
    return made


OPEN = {"sign-in", "sign-out"}
OPERATORS = {
    "books",
    "discrepancies",
    "discrepancy",
    "propose",
    "approve",
    "reject",
}
DEVELOPERS = {
    "new-payment",
    "new-key",
    "revoke-key",
    "new-endpoint",
    "endpoint-secret",
    "remove-endpoint",
}
REFUNDERS = {"new-refund"}
OWNERS = {"activity"}
DEMOS = {"become", "reconcile", "switch-inbox"}


def allowed(role: str, name: str) -> bool:
    if name == "start":
        return True
    if name in DEMOS:
        return False
    if role == "operator" or name in OPERATORS:
        return role == "operator" and name in OPERATORS
    if name in DEVELOPERS:
        return role in ("owner", "developer")
    if name in REFUNDERS:
        return role in ("owner", "developer", "support")
    return role == "owner" if name in OWNERS else True


@pytest.mark.parametrize("role", User.Role.values)
def test_each_role_reaches_only_its_pages(shop: Shop, role: str) -> None:
    user = operator() if role == "operator" else shop.user(role)
    for name, path in paths(shop, discrepancy()).items():
        if name in OPEN:
            continue
        code = browser(user).get(path).status_code
        wanted = (302 if name == "start" else 200) if allowed(role, name) else 403
        assert code == wanted, (role, name)


@pytest.mark.parametrize("role", ["viewer", "support", "operator"])
def test_a_forbidden_form_changes_nothing(shop: Shop, role: str) -> None:
    user = operator() if role == "operator" else shop.user(role)
    for name, path in paths(shop, discrepancy()).items():
        if name not in OPEN and not allowed(role, name):
            assert browser(user).post(path, {"password": PASSWORD}).status_code in (
                403,
                405,
            ), name
    assert ApiKey.objects.filter(revoked_at=None).count() == 1
    assert Endpoint.objects.filter(removed_at=None).count() == 1
    assert not Action.objects.exists()


def test_a_stranger_is_asked_to_sign_in_everywhere(shop: Shop) -> None:
    for name, path in paths(shop, discrepancy()).items():
        if name in OPEN:
            continue
        for response in (Client().get(path), Client().post(path)):
            assert response.status_code in (302, 405), name
            if response.status_code == 302:
                assert response["Location"].startswith("/app/sign-in?next=")
        sheet = Client(headers=HTMX).get(path)
        assert sheet["HX-Redirect"] == "/app/sign-in"


def test_a_merchant_never_reaches_what_is_another_merchants(shop: Shop) -> None:
    Action.objects.create(
        user=shop.user("owner"), what="key_created", subject="localhost/shop"
    )
    owner = Shop("Other").user("owner")
    hers = {shop.payment, shop.endpoint, shop.delivery, shop.key}
    ids = {str(thing.pk) for thing in hers}
    for name, path in paths(shop, discrepancy()).items():
        if not ids & set(path.split("/")):
            continue
        for response in (browser(owner).get(path), browser(owner).post(path)):
            assert response.status_code in (404, 405), name
    for path in ("overview", "payments", "balance", "webhooks", "keys", "activity"):
        text = browser(owner).get(f"/app/{path}").content.decode()
        assert str(shop.payment.id) not in text
        assert "localhost/shop" not in text


def test_every_page_holds_its_own_scripts_only(shop: Shop) -> None:
    response = browser(shop.user("owner")).get("/app/overview")
    policy = response["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "unsafe" not in policy
    text = response.content.decode()
    assert " style=" not in text
    assert "<script>" not in text
    assert " onclick" not in text
    assert "no-store" in response["Cache-Control"]


# Signing in


def sign_in(client: Client, password: str = PASSWORD, **more: Any) -> Any:
    email = more.pop("email", "owner@shop.test")
    return client.post("/app/sign-in", {"email": email, "password": password}, **more)


def test_signing_in_and_out(shop: Shop, client: Client) -> None:
    shop.user("owner")
    before = client.session.session_key
    response = sign_in(client, email=" Owner@Shop.test")
    assert response["Location"] == "/app/"
    assert client.session.session_key != before
    assert client.get("/app/")["Location"] == "/app/overview"
    assert Action.objects.get().what == "signed_in"
    assert not Attempt.objects.exists()
    assert client.get("/app/sign-out").status_code == 405
    client.post("/app/sign-out")
    assert client.get("/app/overview").status_code == 302


def test_a_wrong_password_and_an_unknown_email_look_the_same(
    shop: Shop, client: Client
) -> None:
    shop.user("owner")
    gone = shop.user("viewer")
    gone.is_active = False
    gone.save()
    answers = [
        sign_in(client, "wrong"),
        sign_in(client, email="nobody@shop.test"),
        sign_in(client, email=gone.email),
    ]
    assert {a.status_code for a in answers} == {422}
    for answer in answers:
        assert list(answer.context["form"].non_field_errors()) == [
            "The email or the password is not right."
        ]


def test_too_many_wrong_passwords_are_refused_for_a_while(
    shop: Shop, client: Client
) -> None:
    shop.user("owner")
    for _ in range(5):
        sign_in(client, "wrong")
    assert "Too many tries" in sign_in(client).content.decode()
    # From elsewhere, the same person can still sign in.
    assert sign_in(Client(), REMOTE_ADDR="10.0.0.9").status_code == 302
    Attempt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
    assert sign_in(client).status_code == 302


def test_tries_from_everywhere_on_one_email_are_limited(shop: Shop) -> None:
    shop.user("owner")
    Attempt.objects.bulk_create(
        Attempt(email="owner@shop.test", address=f"10.0.0.{n}") for n in range(50)
    )
    assert sign_in(Client()).status_code == 422


def test_tries_from_one_address_on_many_emails_are_limited(shop: Shop) -> None:
    shop.user("owner")
    Attempt.objects.bulk_create(
        Attempt(email=f"{n}@shop.test", address="127.0.0.1") for n in range(30)
    )
    assert sign_in(Client()).status_code == 422


def test_the_address_header_is_only_believed_on_vercel(
    shop: Shop, client: Client, settings: Any
) -> None:
    spoofed = {"X-Real-IP": "1.2.3.4"}
    sign_in(client, "wrong", headers=spoofed)
    settings.ON_VERCEL = True
    sign_in(client, "wrong", headers=spoofed)
    found = Attempt.objects.order_by("id").values_list("address", flat=True)
    assert list(found) == ["127.0.0.1", "1.2.3.4"]


@pytest.mark.parametrize(
    ("wanted", "got"),
    [
        ("/app/keys", "/app/keys"),
        ("https://evil.test/", "/app/"),
        ("//evil.test/", "/app/"),
        ("javascript:alert(1)", "/app/"),
    ],
)
def test_signing_in_never_leads_to_another_site(
    shop: Shop, client: Client, wanted: str, got: str
) -> None:
    shop.user("owner")
    response = client.post(
        f"/app/sign-in?next={wanted}",
        {"email": "owner@shop.test", "password": PASSWORD},
    )
    assert response["Location"] == got


def test_a_session_ends_after_twelve_hours_however_active(shop: Shop) -> None:
    client = browser(shop.user("owner"))
    session = client.session
    session[STARTED] = time.time() - MAX_SESSION - 1
    session.save()
    assert client.get("/app/overview").status_code == 302


def test_someone_made_inactive_is_out_at_once(shop: Shop) -> None:
    user = shop.user("owner")
    client = browser(user)
    User.objects.update(is_active=False)
    assert client.get("/app/overview").status_code == 302


def test_forms_from_another_site_are_refused(shop: Shop) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(shop.user("owner"))
    session = client.session
    session[STARTED] = time.time()
    session.save()
    path = f"/app/keys/{shop.key.id}/revoke"
    assert client.post(path).status_code == 403


def test_a_user_belongs_to_a_merchant_unless_an_operator(shop: Shop) -> None:
    with pytest.raises(IntegrityError):
        User.objects.create_user("a@b.test", PASSWORD, role="owner")


def test_users_are_created_by_command(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    given = iter(["short", PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda _: next(given))
    args = ["create_user", "New@Shop.test", "New", "support"]
    with pytest.raises(CommandError, match="belongs to a merchant"):
        call_command(*args)
    merchant = f"--merchant={shop.merchant.id}"
    with pytest.raises(CommandError, match="too short"):
        call_command(*args, merchant)
    call_command(*args, merchant)
    assert sign_in(Client(), email="new@shop.test").status_code == 302


# Payments


def test_the_overview_adds_up(shop: Shop) -> None:
    payments.fail(payments.create(shop.merchant, 5_000, "no").id)
    payments.create(shop.merchant, 7_000, "later")
    context = browser(shop.user("viewer")).get("/app/overview").context
    assert context["balance"] == 8_800
    assert context["volume"] == 10_000
    assert context["rate"] == 50
    assert context["pending"] == 1
    bars = context["chart"]["bars"]
    assert [b["amount"] for b in bars if b["amount"]] == [10_000]
    assert bars[-1]["day"] == timezone.localdate()


def test_payments_are_filtered_and_searched(shop: Shop) -> None:
    payments.create(shop.merchant, 7_000, "Later")
    with ago(timedelta(days=10)):
        old = shop.pay(100, "old")
    client = browser(shop.user("viewer"))

    def listed(query: str) -> list[str]:
        found = client.get(f"/app/payments?{query}").context["payments"]
        return [p.reference for p in found]

    assert listed("") == ["Later", "order-1", "old"]
    assert listed("status=pending") == ["Later"]
    assert listed("period=7&status=succeeded") == ["order-1"]
    assert listed("q=LATE") == ["Later"]
    assert listed(f"q={old.id}") == ["old"]
    assert listed("status=nonsense&period=x&after=nonsense") == [
        "Later",
        "order-1",
        "old",
    ]
    assert listed("q=%00") == ["Later", "order-1", "old"]


def test_every_payment_is_listed_once_over_the_pages(shop: Shop) -> None:
    for n in range(45):
        payments.create(shop.merchant, 100, f"p{n}")
    client, seen, after = browser(shop.user("viewer")), [], ""
    while True:
        response = client.get(f"/app/payments?after={after}", headers=HTMX)
        assert ("<html" in response.content.decode()) == (after == "")
        seen += [p.reference for p in response.context["payments"]]
        if response.context["last"] is None:
            break
        after = response.context["last"].id
    assert len(seen) == len(set(seen)) == 46


def test_the_balance_page_adds_up(shop: Shop) -> None:
    refunds.fail(shop.refund.id)
    context = browser(shop.user("viewer")).get("/app/balance").context
    assert [m["amount"] for m in context["moves"]] == [1_000, -1_000, 9_800]
    assert {m["payment"] for m in context["moves"]} == {
        shop.payment.id,
        str(shop.payment.id),
    }
    assert context["balance"] == 9_800


def test_a_payment_is_created_once_however_often_the_form_is_sent(shop: Shop) -> None:
    client = browser(shop.user("owner"), htmx=True)
    sent = {
        "once": str(uuid.uuid4()),
        "amount": "12 500",
        "reference": "order-2",
        "scenario": "slow",
    }
    first, again = (client.post("/app/payments/new", sent) for _ in range(2))
    made = Payment.objects.get(reference="order-2")
    assert (made.amount, made.scenario) == (12_500, "slow")
    assert first["HX-Redirect"] == again["HX-Redirect"] == f"/app/payments/{made.id}"
    assert Action.objects.count() == 1
    changed = client.post("/app/payments/new", sent | {"amount": "1"})
    assert changed.status_code == 422
    assert Payment.objects.count() == 2


@pytest.mark.parametrize(
    "wrong",
    [
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "1.5"},
        {"amount": "10000001"},
        {"reference": ""},
        {"reference": "a\x01b"},
        {"scenario": "x"},
        {"once": "x"},
    ],
)
def test_a_payment_that_makes_no_sense_is_refused(
    shop: Shop, wrong: dict[str, str]
) -> None:
    sent = {
        "once": str(uuid.uuid4()),
        "amount": "100",
        "reference": "r",
        "scenario": "normal",
    }
    client = browser(shop.user("owner"))
    assert client.post("/app/payments/new", sent | wrong).status_code == 422
    assert Payment.objects.count() == 1


def test_a_refund_is_made_once_and_never_above_what_is_left(shop: Shop) -> None:
    client = browser(shop.user("support"), htmx=True)
    path = f"/app/payments/{shop.payment.id}/refund"
    assert client.get(path).context["form"]["amount"].value() == 9_000
    sent = {
        "once": str(uuid.uuid4()),
        "amount": "4000",
        "reason": "duplicate",
        "scenario": "normal",
    }
    for _ in range(2):
        assert client.post(path, sent).status_code == 204
    assert Refund.objects.count() == 2
    more = sent | {"once": str(uuid.uuid4()), "amount": "5001"}
    refused = client.post(path, more)
    assert "more than is left" in refused.content.decode()
    # A form of another payment, with the same number, is another request.
    other = shop.pay(50_000, "other")
    assert client.post(f"/app/payments/{other.id}/refund", sent).status_code == 422
    assert Refund.objects.count() == 2


def test_a_waiting_payment_asks_again_by_itself_only_for_a_while(shop: Shop) -> None:
    waiting = payments.create(shop.merchant, 100, "w")
    client, path = browser(shop.user("viewer")), f"/app/payments/{waiting.id}"
    assert "every 3s" in client.get(path).content.decode()
    part = client.get(path, headers=HTMX | {"HX-Target": "live"}).content.decode()
    assert "every 3s" in part
    assert "<html" not in part
    with ago(timedelta(minutes=3)):
        waiting = payments.create(shop.merchant, 100, "w")
    text = client.get(f"/app/payments/{waiting.id}").content.decode()
    assert "every 3s" not in text


def test_what_merchants_write_is_shown_as_text(shop: Shop) -> None:
    payments.create(shop.merchant, 100, "<img src=x onerror=alert(1)>")
    text = browser(shop.user("viewer")).get("/app/payments").content.decode()
    assert "<img src=x" not in text
    assert "&lt;img src=x" in text


def test_no_sheet_inherits_how_a_waiting_payment_is_replaced() -> None:
    for path in Path("templates/dashboard").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        wanted = text.count('hx-target="#sheet-body" hx-swap="innerHTML"')
        assert text.count('hx-target="#sheet-body"') == wanted, path


def test_a_sheet_is_a_whole_page_without_javascript(shop: Shop) -> None:
    client, path = browser(shop.user("owner")), "/app/payments/new"
    page = client.get(path).content.decode()
    assert "<title>New payment · TallyPay</title>" in page
    assert "<html" not in client.get(path, headers=HTMX).content.decode()


def test_refusals_are_said_in_words(shop: Shop) -> None:
    viewer = browser(shop.user("viewer"))
    page = viewer.get("/app/keys/new").content.decode()
    assert "Your role does not allow this." in page
    page = viewer.get("/app/nothing").content.decode()
    assert "There is nothing at this address." in page


# Keys and webhooks


def test_a_new_key_is_shown_once_and_works(shop: Shop) -> None:
    client = browser(shop.user("developer"), htmx=True)
    sent = {"name": "Till", "password": PASSWORD}
    assert client.post("/app/keys/new", sent | {"password": "x"}).status_code == 422
    assert ApiKey.objects.count() == 1
    value = client.post("/app/keys/new", sent).context["value"]
    assert keys.owner(value) == shop.merchant
    text = client.get("/app/keys").content.decode()
    assert "Till" in text
    assert value not in text
    made = ApiKey.objects.get(name="Till")
    assert client.post(f"/app/keys/{made.id}/revoke").status_code == 204
    assert keys.owner(value) is None
    assert client.post(f"/app/keys/{made.id}/revoke").status_code == 404
    done = Action.objects.order_by("id").values_list("what", flat=True)
    assert list(done) == ["key_created", "key_revoked"]


def test_guessing_a_password_in_a_sheet_is_limited_too(shop: Shop) -> None:
    client = browser(shop.user("owner"))
    for _ in range(5):
        client.post("/app/keys/new", {"name": "x", "password": "wrong"})
    sent = {"name": "x", "password": PASSWORD}
    assert "Too many tries" in client.post("/app/keys/new", sent).content.decode()
    assert ApiKey.objects.count() == 1


def test_a_webhook_address_is_added_read_and_removed(shop: Shop) -> None:
    client = browser(shop.user("developer"), htmx=True)
    refused = client.post("/app/webhooks/new", {"url": "ftp://example.com"})
    assert refused.status_code == 422
    shown = client.post("/app/webhooks/new", {"url": "http://localhost/b"})
    made = Endpoint.objects.get(url="http://localhost/b")
    assert shown.context["value"] == signing.secret_of(made.id)
    path = f"/app/webhooks/{made.id}/secret"
    assert client.post(path, {"password": "x"}).status_code == 422
    again = client.post(path, {"password": PASSWORD})
    assert again.context["value"] == signing.secret_of(made.id)
    assert client.post(f"/app/webhooks/{made.id}/remove").status_code == 204
    assert client.get(path).status_code == 404


def test_a_delivery_is_sent_again_only_by_those_who_may(shop: Shop) -> None:
    Delivery.objects.update(status="failed")
    path = f"/app/deliveries/{shop.delivery.id}"
    browser(shop.user("viewer")).post(path)
    assert Delivery.objects.get().status == "failed"
    browser(shop.user("developer")).post(path)
    assert Delivery.objects.get().status == "pending"
    again = browser(shop.user("developer")).post(path)
    assert "still being sent" in again.content.decode()


# Operators


def test_two_operators_resolve_a_discrepancy(shop: Shop) -> None:
    found = discrepancy()
    ange, merveille = (browser(operator(n)) for n in ("ange", "merveille"))
    path = f"/app/discrepancies/{found.id}"
    sent = {"action": "write_off", "note": "Too small to chase"}
    assert ange.post(f"{path}/propose", sent | {"action": "credit"}).status_code == 422
    assert ange.post(f"{path}/propose", sent | {"note": ""}).status_code == 422
    assert ange.post(f"{path}/propose", sent).status_code == 302
    own = ange.post(f"{path}/approve", {"proposal": 1, "password": PASSWORD})
    assert "Someone else must approve it." in own.content.decode()
    page = merveille.get(f"{path}/approve").content.decode()
    assert "Too small to chase" in page
    assert "Take the difference on ourselves" in page
    for wrong in ({"password": "x"}, {"proposal": 2}, {"proposal": ""}):
        answer = merveille.post(
            f"{path}/approve", {"proposal": 1, "password": PASSWORD} | wrong
        )
        assert answer.status_code == 422
    found.refresh_from_db()
    assert found.status == "proposed"
    merveille.post(f"{path}/approve", {"proposal": 1, "password": PASSWORD})
    found.refresh_from_db()
    assert (found.status, found.approved_by) == ("resolved", "merveille@tallypay.test")
    for client in (ange, merveille):
        text = client.get(path).content.decode()
        assert "Ms operator" in text


def test_a_proposal_is_rejected_then_made_again(shop: Shop) -> None:
    found = discrepancy()
    client, path = browser(operator()), f"/app/discrepancies/{found.id}"
    client.post(f"{path}/propose", {"action": "write_off", "note": "x"})
    assert client.post(f"{path}/reject", {"proposal": "x"}).status_code == 422
    assert client.post(f"{path}/reject", {"proposal": 1}).status_code == 302
    assert client.post(f"{path}/reject", {"proposal": 1}).status_code == 422
    assert "was not carried out" in client.get(path).content.decode()


def test_the_books_page_says_whether_they_balance(shop: Shop) -> None:
    context = browser(operator()).get("/app/books").context
    assert context["difference"] == 0
    shown = {account.code: amount for account, amount in context["accounts"]}
    assert shown == {"provider": 10_000, "fees": 200, "refunds": 1_000}
    assert [amount for _, amount in context["merchants"]] == [8_800]


def test_a_payment_shows_its_entries_which_balance(shop: Shop) -> None:
    refunds.succeed(shop.refund.id)
    client = browser(shop.user("viewer"))
    entries = client.get(f"/app/payments/{shop.payment.id}").context["entries"]
    assert [entry["debit"] for entry in entries] == [10_000, 1_000, 1_000]
    for entry in entries:
        assert entry["debit"] == entry["credit"]
    accounts = [str(line["account"]) for line in entries[0]["lines"]]
    assert accounts == ["Held by the provider", "Fees", shop.merchant.name]


def test_a_refund_moved_wrongly_is_shown_as_money_out(shop: Shop) -> None:
    refunds.succeed(shop.refund.id)
    Discrepancy.objects.create(
        kind="wrong_amount", reference=shop.refund.id, operation="refund", amount=-500
    )
    StatementLine.objects.create(
        reference=shop.refund.id,
        kind="refund",
        amount=1_500,
        moved_at=timezone.now(),
    )
    row = browser(operator()).get("/app/discrepancies").context["discrepancies"][0]
    assert (row.ours, row.theirs) == (-1_000, -1_500)
    assert row.paid == shop.payment
    unknown = Discrepancy.objects.create(
        kind="unknown", reference=uuid.uuid4(), operation="charge", amount=5
    )
    page = browser(operator("b")).get(f"/app/discrepancies/{unknown.id}")
    assert page.status_code == 200


def test_the_balance_names_each_payment(shop: Shop) -> None:
    page = browser(shop.user("viewer")).get("/app/balance").content.decode()
    assert f"<small>{shop.payment.reference}</small>" in page

import hashlib
import hmac
import json
import time
import uuid
from base64 import urlsafe_b64encode
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from pytest_django.fixtures import Settings
from qstash.message import MessageApi

from wakeups import qstash
from wakeups.models import WakeUp

pytestmark = pytest.mark.django_db
URL = "https://tallypay.test/internal/wake"
BODY = json.dumps({"id": str(uuid.uuid4()), "due_at": "2026-11-23T12:00:00+00:00"})
CURRENT = "current-key-for-tests-of-at-least-32-bytes"
NEXT = "next-key-for-tests-of-at-least-32-bytes"


@pytest.fixture(autouse=True)
def keys(settings: Settings) -> None:
    settings.PUBLIC_URL = "https://tallypay.test"
    settings.QSTASH_CURRENT_SIGNING_KEY = CURRENT
    settings.QSTASH_NEXT_SIGNING_KEY = NEXT


def encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(body: str = BODY, key: str = CURRENT, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": "Upstash",
        "sub": URL,
        "exp": now + 300,
        "nbf": now - 1,
        "iat": now - 1,
        "jti": "jwt_test",
        "body": encode(hashlib.sha256(body.encode()).digest()),
    } | claims
    parts = [{"alg": "HS256", "typ": "JWT"}, payload]
    text = ".".join(encode(json.dumps(part).encode()) for part in parts)
    signature = hmac.new(key.encode(), text.encode(), hashlib.sha256).digest()
    return f"{text}.{encode(signature)}"


def call(client: Client, signature: str, body: str = BODY) -> int:
    return client.post(
        "/internal/wake",
        body,
        content_type="application/json",
        headers={"Upstash-Signature": signature},
    ).status_code


@pytest.mark.parametrize("key", [CURRENT, NEXT])
def test_a_signed_call_is_recorded_with_either_key(client: Client, key: str) -> None:
    assert call(client, sign(key=key)) == 204
    assert str(WakeUp.objects.get().id) == json.loads(BODY)["id"]


def test_the_same_call_twice_is_recorded_once(client: Client) -> None:
    assert [call(client, sign()) for _ in range(2)] == [204, 204]
    assert WakeUp.objects.count() == 1


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param("", id="missing"),
        pytest.param("a.b.c", id="garbage"),
        pytest.param(sign(key="another-key-of-at-least-32-bytes-long"), id="wrong key"),
        pytest.param(sign(body="{}"), id="other body"),
        pytest.param(sign(sub="https://elsewhere.test"), id="other address"),
        pytest.param(sign(exp=int(time.time()) - 600), id="expired"),
        pytest.param(sign(iss="Someone"), id="other issuer"),
    ],
)
def test_any_other_call_is_refused(client: Client, signature: str) -> None:
    assert call(client, signature) == 401
    assert not WakeUp.objects.exists()


def test_nothing_is_accepted_when_the_keys_are_not_set(
    client: Client, settings: Settings
) -> None:
    settings.QSTASH_CURRENT_SIGNING_KEY = settings.QSTASH_NEXT_SIGNING_KEY = ""
    assert call(client, sign(key="")) == 401


@pytest.mark.parametrize("body", ["{}", "[]", "not json", '{"id": 1, "due_at": 2}'])
def test_a_signed_call_that_makes_no_sense_is_refused(
    client: Client, body: str
) -> None:
    assert call(client, sign(body), body) == 401


def test_a_call_is_asked_for_after_the_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setattr(
        MessageApi, "publish_json", lambda self, **options: sent.update(options)
    )
    wakeup_id = qstash.schedule(timedelta(minutes=2))
    assert sent["url"] == URL
    assert sent["delay"] == "120s"
    assert sent["body"]["id"] == str(wakeup_id)


def test_the_pages_answer(client: Client) -> None:
    assert b"None yet" in client.get("/").content
    call(client, sign())
    assert b"Received" in client.get("/last-wakeup").content
    assert client.get("/healthz").json() == {"status": "ok"}

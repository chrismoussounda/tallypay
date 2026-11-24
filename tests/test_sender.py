import base64
import hashlib
import hmac
import socket
import threading
import time
import uuid
from typing import Any

import pytest
from pytest_django.fixtures import Settings

from webhooks import sender, signing

from .receiver import Receiver


def resolves_to(monkeypatch: pytest.MonkeyPatch, *ips: str) -> None:
    found = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: found)


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Where a connection was tried. None succeeds."""
    tried: list[Any] = []

    def create_connection(address: Any, *args: Any) -> None:
        tried.append(address)
        raise OSError("closed")

    monkeypatch.setattr(socket, "create_connection", create_connection)
    return tried


def test_the_signature_is_the_standard_one() -> None:
    secret, message = signing.secret_of(uuid.uuid4()), uuid.uuid4()
    headers = signing.headers(secret, message, 1_700_000_000, b'{"a":1}')
    key = base64.b64decode(secret.split("_")[1])
    signed = f'{message}.1700000000.{{"a":1}}'.encode()
    expected = hmac.new(key, signed, hashlib.sha256).digest()
    assert headers["Webhook-Signature"] == "v1," + base64.b64encode(expected).decode()


def test_each_endpoint_has_its_own_secret() -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    assert signing.secret_of(mine) == signing.secret_of(mine)
    assert signing.secret_of(mine) != signing.secret_of(theirs)


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example/hook",
        "ftp://shop.example",
        "https://shop.example:22/",
        "https://shop.example:99999/",
        "https://user:pass@shop.example/",
        "https://shop.example/#part",
        "https://shop.example/a b",
        "https://shop.example/\r\nX-Evil: 1",
        "https://sh\u00f6p.example/",
        "https:///hook",
        "https://[::1",
        "/hook",
        "",
    ],
)
def test_other_addresses_are_refused(settings: Settings, url: str) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False
    with pytest.raises(sender.Refused):
        sender.check(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "255.255.255.255",
    ],
)
def test_internal_addresses_are_refused_whatever_their_name(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, connected: list[Any], ip: str
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False
    for ips in ([ip], ["93.184.216.34", ip]):
        resolves_to(monkeypatch, *ips)
        with pytest.raises(sender.Refused):
            sender.send("https://shop.example/hook", {}, b"{}")
    assert connected == []


def test_it_connects_to_the_very_address_it_checked(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, connected: list[Any]
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False
    resolves_to(monkeypatch, "93.184.216.34")
    with pytest.raises(sender.Unreachable):
        sender.send("https://shop.example/hook", {}, b"{}")
    assert connected == [("93.184.216.34", 443)]


def test_a_message_arrives_as_sent(settings: Settings) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    with Receiver().running() as shop:
        assert sender.send(shop.url, {"Webhook-Id": "x"}, b'{"a":1}') == 200
    headers, body = shop.got[0]
    assert headers["webhook-id"] == "x"
    assert headers["content-type"] == "application/json"
    assert body == b'{"a":1}'


def test_a_redirect_is_not_followed(settings: Settings) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    with Receiver().running() as shop:
        shop.answers = [302]
        assert sender.send(shop.url, {}, b"{}") == 302
    assert len(shop.got) == 1


def test_a_server_that_is_down_is_unreachable(settings: Settings) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    with Receiver().running() as shop:
        pass
    with pytest.raises(sender.Unreachable):
        sender.send(shop.url, {}, b"{}")


def test_a_server_that_never_answers_is_given_up_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    monkeypatch.setattr(sender, "TIMEOUT", 0.2)
    with socket.create_server(("127.0.0.1", 0)) as silent:
        url = f"http://127.0.0.1:{silent.getsockname()[1]}/"
        with pytest.raises(sender.Unreachable, match="in time"):
            sender.send(url, {}, b"{}")


@pytest.mark.parametrize(
    "start", [b"", b"HTTP/1.1 200 OK\r\n"], ids=["status", "headers"]
)
def test_a_server_that_drips_its_answer_is_cut_off(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, start: bytes
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = True
    monkeypatch.setattr(sender, "LIMIT", 0.5)
    stop = threading.Event()

    def drip(server: socket.socket) -> None:
        client, _ = server.accept()
        with client:
            client.sendall(start)
            while not stop.wait(0.05):
                try:
                    client.sendall(b"x")
                except OSError:
                    return

    with socket.create_server(("127.0.0.1", 0)) as server:
        thread = threading.Thread(target=drip, args=[server])
        thread.start()
        began = time.monotonic()
        try:
            with pytest.raises(sender.Unreachable, match="in time"):
                sender.send(f"http://127.0.0.1:{server.getsockname()[1]}/", {}, b"{}")
            assert time.monotonic() - began < 3
        finally:
            stop.set()
            thread.join()


def test_a_name_that_takes_too_long_to_look_up_is_given_up_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.WEBHOOKS_ALLOW_LOCAL = False
    done = threading.Event()
    monkeypatch.setattr(sender, "TIMEOUT", 0.2)
    monkeypatch.setattr(sender, "_resolve", lambda *a: done.wait(5))
    began = time.monotonic()
    with pytest.raises(sender.Unreachable, match="in time"):
        sender.send("https://shop.example/", {}, b"{}")
    assert time.monotonic() - began < 2
    done.set()

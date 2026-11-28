import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_the_site_is_light_until_dark_is_chosen() -> None:
    client = Client()
    assert 'data-theme="light"' in client.get("/").content.decode()
    answer = client.post("/theme", {"theme": "dark", "next": "/about"})
    assert answer["Location"] == "/about"
    assert 'data-theme="dark"' in client.get("/").content.decode()
    client.post("/theme", {"theme": "light", "next": "/"})
    assert 'data-theme="light"' in client.get("/").content.decode()


def test_header_buttons_keep_a_name_when_shortened_on_phones() -> None:
    client = Client()
    page = client.get("/").content.decode()
    assert 'aria-label="TallyPay"' in page
    assert 'value="dark" aria-label="Dark"' in page
    client.post("/theme", {"theme": "dark", "next": "/"})
    assert 'value="light" aria-label="Light"' in client.get("/").content.decode()


def test_the_theme_switch_never_sends_elsewhere() -> None:
    answer = Client().post(
        "/theme", {"theme": "dark", "next": "https://elsewhere.example/"}
    )
    assert answer["Location"] == "/"

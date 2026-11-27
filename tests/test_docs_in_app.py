import pytest
from django.test import Client

from .test_demos import visit

pytestmark = pytest.mark.django_db


def test_how_it_works_is_read_inside_the_app() -> None:
    client = visit()
    page = client.get("/app/docs").content.decode()
    assert "How I built TallyPay" in page
    assert 'class="tabs"' in page
    assert "try the demo" not in page


def test_how_it_works_needs_someone_signed_in() -> None:
    assert Client().get("/app/docs").status_code == 302

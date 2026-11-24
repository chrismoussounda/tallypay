import ast
import gettext
import io
import re
from pathlib import Path

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from django.test import Client

from tests.test_dashboard import Shop, browser

ROOT = Path(__file__).parent.parent
PO = ROOT / "locale/fr/LC_MESSAGES/django.po"


def used() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        found |= {m[1] for m in re.findall(r"""translate (["'])(.*?)\1 *%}""", text)}
        found |= set(re.findall(r'_\("(.*?)"\)', text))
        for one in re.findall(r"{% blocktranslate .*?%}(.*?){% plural", text):
            found.add(re.sub(r"{{ (\w+) }}", r"%(\1)s", one))
    for path in (ROOT / "dashboard").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and isinstance(node.args[0], ast.Constant)
            ):
                found.add(str(node.args[0].value))
    # Why an address was refused, said by the sender.
    sender = (ROOT / "webhooks/sender.py").read_text(encoding="utf-8")
    return found | set(re.findall(r'Refused\("(.*?)"\)', sender))


def test_every_word_is_in_french_and_compiled() -> None:
    with PO.open("rb") as file:
        catalog = read_po(file)
    known = {
        str(message.id[0] if isinstance(message.id, tuple) else message.id)
        for message in catalog
        if message.id and message.string
    }
    assert used() - known == set()
    compiled = io.BytesIO()
    write_mo(compiled, catalog)
    compiled.seek(0)
    fresh = gettext.GNUTranslations(compiled)
    with PO.with_suffix(".mo").open("rb") as file:
        stored = gettext.GNUTranslations(file)
    # If not: uv run pybabel compile -D django -d locale
    for message in known:
        for n in (1, 2):
            assert stored.ngettext(message, "", n) == fresh.ngettext(message, "", n)
        assert stored.gettext(message) == fresh.gettext(message)


@pytest.mark.django_db
def test_the_language_is_the_browsers_until_chosen() -> None:
    client = Client(headers={"Accept-Language": "fr-CG,fr;q=0.9"})
    assert "Se connecter" in client.get("/app/sign-in").content.decode()
    client.post("/i18n/setlang/", {"language": "en", "next": "/app/sign-in"})
    assert "Sign in" in client.get("/app/sign-in").content.decode()


@pytest.mark.django_db
def test_amounts_are_written_as_the_language_does() -> None:
    client = browser(Shop().user("viewer"))
    for language, shown in (("fr", "8\u00a0800\u00a0XAF"), ("en", "8,800\u00a0XAF")):
        text = client.get("/app/balance", headers={"Accept-Language": language})
        assert shown in text.content.decode().replace("\u202f", "\u00a0")

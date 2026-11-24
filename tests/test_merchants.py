from io import StringIO

import pytest
from django.core.management import call_command

from ledger.models import Account
from merchants import keys
from merchants.models import ApiKey

pytestmark = pytest.mark.django_db


def test_a_merchant_has_an_account_for_what_it_is_owed() -> None:
    merchant = keys.create_merchant("Shop")
    assert merchant.account.kind == Account.Kind.LIABILITY


def test_a_key_opens_its_own_merchant_only() -> None:
    mine, theirs = keys.create_merchant("Mine"), keys.create_merchant("Theirs")
    key = keys.issue(mine)
    keys.issue(theirs)
    assert keys.owner(key) == mine
    assert keys.owner(key + "x") is None
    assert keys.owner("") is None


def test_a_key_is_not_kept() -> None:
    key = keys.issue(keys.create_merchant("Shop"))
    assert key not in str(ApiKey.objects.values().get())


def test_a_revoked_key_opens_nothing_while_the_others_still_do() -> None:
    merchant = keys.create_merchant("Shop")
    old, new = keys.issue(merchant), keys.issue(merchant)
    keys.revoke(ApiKey.objects.get(ending=old[-4:]))
    assert keys.owner(old) is None
    assert keys.owner(new) == merchant


def test_the_command_shows_a_key_that_works() -> None:
    out = StringIO()
    call_command("create_merchant", "Shop", stdout=out)
    merchant = keys.owner(out.getvalue().split()[1])
    assert merchant is not None
    assert merchant.name == "Shop"

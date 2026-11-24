import hashlib
import secrets

from django.db import transaction
from django.utils import timezone

from ledger.models import Account

from .models import ApiKey, Merchant


def _hash(key: str) -> str:
    # Keys are long and random, so a slow password hash would add nothing.
    return hashlib.sha256(key.encode()).hexdigest()


@transaction.atomic
def create_merchant(name: str) -> Merchant:
    merchant = Merchant(name=name)
    merchant.account = Account.objects.create(
        code=f"merchant:{merchant.id}", name=name, kind=Account.Kind.LIABILITY
    )
    merchant.save()
    return merchant


def issue(merchant: Merchant, name: str = "") -> str:
    """A new key, shown this once: only its hash is kept."""
    key = f"tp_test_{secrets.token_urlsafe(32)}"
    ApiKey.objects.create(
        merchant=merchant, name=name, key_hash=_hash(key), ending=key[-4:]
    )
    return key


def revoke(key: ApiKey) -> None:
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])


def owner(key: str) -> Merchant | None:
    found = (
        ApiKey.objects.filter(key_hash=_hash(key), revoked_at=None)
        .select_related("merchant")
        .first()
    )
    return found.merchant if found else None

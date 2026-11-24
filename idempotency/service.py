import hashlib
import json
from collections.abc import Callable
from typing import Any

from django.db import transaction

from merchants.models import Merchant

from .models import IdempotencyRecord


class KeyReused(Exception):
    """The same key came with a different request."""


@transaction.atomic
def once(
    merchant: Merchant,
    key: str,
    request: dict[str, Any],
    action: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """The action's answer, and whether it is the one given the first time.

    The key and what the action writes are committed together. A simultaneous
    request with the same key waits here for the first to commit or roll back.
    """
    text = json.dumps(request, sort_keys=True)
    request_hash = hashlib.sha256(text.encode()).hexdigest()
    record, first = IdempotencyRecord.objects.get_or_create(
        merchant=merchant, key=key, defaults={"request_hash": request_hash}
    )
    if not first:
        if record.request_hash != request_hash:
            raise KeyReused
        response: dict[str, Any] = record.response
        return response, True
    response = action()
    record.response = response
    record.save(update_fields=["response"])
    return response, False

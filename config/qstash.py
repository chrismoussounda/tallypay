import json
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from qstash import QStash, Receiver
from qstash.errors import SignatureError


def url_of(path: str) -> str:
    return settings.PUBLIC_URL.rstrip("/") + path


def publish(path: str, body: dict[str, Any], delay: timedelta) -> None:
    """Asks QStash to call this app back once the delay is over."""
    QStash(settings.QSTASH_TOKEN, base_url=settings.QSTASH_URL).message.publish_json(
        url=url_of(path),
        body=body,
        delay=f"{int(delay.total_seconds())}s",
        deduplication_id=str(uuid.uuid4()),
    )


def read_signed(request: HttpRequest) -> dict[str, Any] | None:
    """The call's content, or None unless QStash signed it for this address."""
    current = settings.QSTASH_CURRENT_SIGNING_KEY
    following = settings.QSTASH_NEXT_SIGNING_KEY
    # Without this, anyone could sign a call with the empty key.
    if not (current and following):
        return None
    try:
        text = request.body.decode()
        Receiver(current_signing_key=current, next_signing_key=following).verify(
            signature=request.headers.get("Upstash-Signature", ""),
            body=text,
            url=url_of(request.path),
        )
        data = json.loads(text)
    except (SignatureError, ValueError, KeyError, AttributeError):
        return None
    return data if isinstance(data, dict) else None

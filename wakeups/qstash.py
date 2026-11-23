import json
import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.urls import reverse
from qstash import QStash, Receiver
from qstash.errors import SignatureError


def wake_url() -> str:
    return settings.PUBLIC_URL.rstrip("/") + reverse("wake")


def schedule(delay: timedelta) -> uuid.UUID:
    """Asks QStash to call this app back once the delay is over."""
    wakeup_id = uuid.uuid4()
    due_at = datetime.now(UTC) + delay
    QStash(settings.QSTASH_TOKEN, base_url=settings.QSTASH_URL).message.publish_json(
        url=wake_url(),
        body={"id": str(wakeup_id), "due_at": due_at.isoformat()},
        delay=f"{int(delay.total_seconds())}s",
        deduplication_id=str(wakeup_id),
    )
    return wakeup_id


def read_signed(signature: str, body: bytes) -> tuple[uuid.UUID, datetime] | None:
    """The call's content, or None unless QStash signed it for this app."""
    current = settings.QSTASH_CURRENT_SIGNING_KEY
    following = settings.QSTASH_NEXT_SIGNING_KEY
    # Without this, anyone could sign a call with the empty key.
    if not (current and following):
        return None
    try:
        text = body.decode()
        Receiver(current_signing_key=current, next_signing_key=following).verify(
            signature=signature, body=text, url=wake_url()
        )
        data = json.loads(text)
        due_at = datetime.fromisoformat(data["due_at"])
        return (uuid.UUID(data["id"]), due_at) if due_at.tzinfo else None
    except (SignatureError, ValueError, KeyError, TypeError, AttributeError):
        return None

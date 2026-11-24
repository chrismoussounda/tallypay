import uuid
from datetime import UTC, datetime, timedelta

from django.http import HttpRequest
from django.urls import reverse

from config import qstash


def wake_url() -> str:
    return qstash.url_of(reverse("wake"))


def schedule(delay: timedelta) -> uuid.UUID:
    wakeup_id = uuid.uuid4()
    due_at = datetime.now(UTC) + delay
    qstash.publish(
        reverse("wake"), {"id": str(wakeup_id), "due_at": due_at.isoformat()}, delay
    )
    return wakeup_id


def read_signed(request: HttpRequest) -> tuple[uuid.UUID, datetime] | None:
    data = qstash.read_signed(request)
    try:
        due_at = datetime.fromisoformat(data["due_at"]) if data else None
        if data is None or due_at is None or due_at.tzinfo is None:
            return None
        return uuid.UUID(data["id"]), due_at
    except (ValueError, KeyError, TypeError, AttributeError):
        return None

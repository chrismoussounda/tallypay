"""Signatures as described by https://www.standardwebhooks.com."""

import base64
import hashlib
import hmac
import uuid

from django.conf import settings


def secret_of(endpoint_id: uuid.UUID) -> str:
    """Derived from the app's own key, so that it is not kept in the database."""
    key = settings.SECRET_KEY.encode()
    digest = hmac.new(key, b"webhook:" + endpoint_id.bytes, hashlib.sha256).digest()
    return "whsec_" + base64.b64encode(digest).decode()


def headers(
    secret: str, message_id: uuid.UUID, timestamp: int, body: bytes
) -> dict[str, str]:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{message_id}.{timestamp}.".encode() + body
    signature = hmac.new(key, signed, hashlib.sha256).digest()
    return {
        "Webhook-Id": str(message_id),
        "Webhook-Timestamp": str(timestamp),
        "Webhook-Signature": "v1," + base64.b64encode(signature).decode(),
    }

from typing import ClassVar

from django.db import models

from merchants.models import Merchant


class IdempotencyRecord(models.Model):
    """What a merchant's key was used for, and what was answered."""

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE)
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["merchant", "key"], name="idempotency_key_once"
            )
        ]

    def __str__(self) -> str:
        return self.key

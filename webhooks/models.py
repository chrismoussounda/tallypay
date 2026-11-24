import uuid
from typing import ClassVar

from django.db import models

from merchants.models import Merchant


class Endpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="endpoints"
    )
    url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.url


class Event(models.Model):
    """Something a merchant is told about, kept as it was at the time."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT)
    type = models.CharField(max_length=40)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.type


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        DELIVERED = "delivered"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name="deliveries"
    )
    endpoint = models.ForeignKey(
        Endpoint, on_delete=models.PROTECT, related_name="deliveries"
    )
    status = models.CharField(
        max_length=10, choices=Status, default=Status.PENDING, db_default="pending"
    )
    tries = models.PositiveIntegerField(default=0)
    # How many tries came before the last replay.
    tries_before = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["event", "endpoint"], name="delivery_once_per_endpoint"
            )
        ]

    def __str__(self) -> str:
        return str(self.id)


class Try(models.Model):
    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="history"
    )
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.status_code or self.error)

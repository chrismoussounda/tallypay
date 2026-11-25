import uuid
from datetime import datetime, timedelta
from typing import ClassVar

from django.db import models

from ledger.models import Account


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    # What is owed to the merchant.
    account = models.OneToOneField(Account, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Demo(models.Model):
    """A merchant that a visitor opened, to try the app."""

    merchant = models.OneToOneField(
        Merchant, on_delete=models.PROTECT, primary_key=True
    )
    # Hashed, and only while it is open.
    address = models.CharField(max_length=64, blank=True)
    used = models.PositiveIntegerField(default=0)
    # Whether its test server answers, or is down.
    inbox_works = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    LASTS = timedelta(hours=24)
    # Payments, refunds and webhooks sent again: each wakes the app a few times.
    MOST = 20

    class Meta:
        indexes: ClassVar = [models.Index(fields=["created_at"], name="demo_newest")]

    def __str__(self) -> str:
        return str(self.merchant_id)

    @property
    def closes_at(self) -> datetime:
        return self.created_at + self.LASTS

    @property
    def left(self) -> int:
        return max(self.MOST - self.used, 0)


class ApiKey(models.Model):
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="keys"
    )
    key_hash = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=60, blank=True, db_default="")
    # Enough to recognise a key, too little to use it.
    ending = models.CharField(max_length=4)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"...{self.ending}"

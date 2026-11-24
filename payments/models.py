import uuid
from typing import ClassVar

from django.db import models
from django.db.models import Q

from ledger.models import Entry
from merchants.models import Merchant


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.BigIntegerField()
    reference = models.CharField(max_length=64)
    status = models.CharField(
        max_length=10, choices=Status, default=Status.PENDING, db_default="pending"
    )
    entry = models.OneToOneField(Entry, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="payment_amount_positive"
            ),
            # In the books exactly when it succeeded.
            models.CheckConstraint(
                condition=Q(status="succeeded", entry__isnull=False)
                | Q(status__in=["pending", "failed"], entry__isnull=True),
                name="payment_in_books_when_succeeded",
            ),
        ]
        indexes: ClassVar = [
            models.Index(
                fields=["merchant", "-created_at", "-id"], name="payment_newest"
            )
        ]

    def __str__(self) -> str:
        return self.reference


class PaymentEvent(models.Model):
    """The timeline of a payment."""

    class Kind(models.TextChoices):
        CREATED = "created"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="events"
    )
    kind = models.CharField(max_length=10, choices=Kind)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.kind

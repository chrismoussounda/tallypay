import uuid
from typing import ClassVar

from django.db import models
from django.db.models import Q

from ledger.models import Entry
from payments.models import Payment
from provider.models import Scenario


class Refund(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    class Reason(models.TextChoices):
        REQUESTED = "requested_by_customer"
        DUPLICATE = "duplicate"
        FRAUDULENT = "fraudulent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="refunds"
    )
    amount = models.BigIntegerField()
    reason = models.CharField(max_length=24, choices=Reason, default=Reason.REQUESTED)
    scenario = models.CharField(
        max_length=20, choices=Scenario, default=Scenario.NORMAL
    )
    status = models.CharField(
        max_length=10, choices=Status, default=Status.PENDING, db_default="pending"
    )
    # Sets the money aside, then sends it out or gives it back to the merchant.
    hold = models.OneToOneField(Entry, on_delete=models.PROTECT, related_name="+")
    settlement = models.OneToOneField(
        Entry, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="refund_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(
                    status="pending", settlement__isnull=True, settled_at__isnull=True
                )
                | Q(
                    status__in=["succeeded", "failed"],
                    settlement__isnull=False,
                    settled_at__isnull=False,
                ),
                name="refund_in_books_when_settled",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)

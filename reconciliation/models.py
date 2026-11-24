import uuid
from typing import ClassVar

from django.db import models
from django.db.models import Q

from ledger.models import Entry


class StatementLine(models.Model):
    """What the provider says moved, kept as received."""

    reference = models.UUIDField(unique=True)
    kind = models.CharField(max_length=10)
    amount = models.BigIntegerField()
    moved_at = models.DateTimeField()
    matched_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.kind} {self.reference}"


class Discrepancy(models.Model):
    class Kind(models.TextChoices):
        UNKNOWN = "unknown", "Money moved that nobody asked for"
        AFTER_FAILURE = "after_failure", "Money moved after it was said to have failed"
        WRONG_AMOUNT = "wrong_amount", "Another amount moved"
        MISSING = "missing", "In our books, not in the statement"
        LATE = "late", "In the statement, after it was written off"
        STUCK = "stuck", "Still waiting, and not in the statement"

    class Status(models.TextChoices):
        OPEN = "open"
        PROPOSED = "proposed"
        RETURNING = "returning"
        RESOLVED = "resolved"

    class Action(models.TextChoices):
        RETURN = "return", "Give the money back to the customer"
        CREDIT = "credit", "Give the money to the merchant"
        WRITE_OFF = "write_off", "Take the difference on ourselves"
        FAIL = "fail", "Call it failed"
        MATCHED = "matched", "Settled by itself"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind)
    reference = models.UUIDField()
    operation = models.CharField(max_length=10)
    # What the provider holds beyond what we expected. Negative if less.
    amount = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status, default=Status.OPEN)
    # Keeps the books equal to the statement, the difference being in suspense.
    booking = models.OneToOneField(
        Entry, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    resolution = models.OneToOneField(
        Entry, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    action = models.CharField(max_length=10, choices=Action, blank=True)
    note = models.CharField(max_length=300, blank=True)
    proposed_by = models.CharField(max_length=60, blank=True)
    approved_by = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "discrepancies"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["kind", "reference"], name="discrepancy_once"
            ),
            # Nobody approves what they proposed.
            models.CheckConstraint(
                condition=Q(approved_by="")
                | Q(approved_by="policy")
                | ~Q(approved_by=models.F("proposed_by")),
                name="discrepancy_two_people",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.reference}"

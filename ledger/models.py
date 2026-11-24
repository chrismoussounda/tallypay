import uuid
from typing import ClassVar

from django.db import models


class Account(models.Model):
    class Kind(models.TextChoices):
        ASSET = "asset"
        LIABILITY = "liability"
        INCOME = "income"
        EXPENSE = "expense"

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=Kind)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["asset", "liability", "income", "expense"]
                ),
                name="account_kind",
            )
        ]

    def __str__(self) -> str:
        return self.code


class Entry(models.Model):
    """One event in the books. Never changed: a mistake is undone by a reversal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    description = models.CharField(max_length=200)
    reverses = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Set by Postgres, which refuses lines from any other transaction.
    transaction_id = models.BigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name_plural = "entries"

    def __str__(self) -> str:
        return self.description


class Line(models.Model):
    """Whole XAF francs: a debit is positive, a credit negative."""

    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    amount = models.BigIntegerField()

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="line_amount_not_zero"
            ),
            models.UniqueConstraint(
                fields=["entry", "account"], name="line_once_per_account"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_id} {self.amount}"

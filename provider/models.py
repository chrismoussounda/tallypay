import uuid

from django.db import models


class Scenario(models.TextChoices):
    """How the simulated provider behaves with a payment."""

    NORMAL = "normal", "Accepts, then calls back"
    DECLINE = "decline", "Declines, then calls back"
    SLOW = "slow", "Takes a minute to decide"
    REQUEST_LOST = "request_lost", "Never gets the first request"
    ANSWER_LOST = "answer_lost", "Gets the request, but its answer is lost"
    DUPLICATE_CALLBACK = "duplicate_callback", "Calls back three times"
    NO_CALLBACK = "no_callback", "Never calls back"

    @classmethod
    def recoverable(cls) -> set[str]:
        """Those that end well without reconciliation."""
        return set(cls.values)


class Kind(models.TextChoices):
    CHARGE = "charge"
    REFUND = "refund"


class Request(models.Model):
    """Each time the provider was asked to charge a payment."""

    reference = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.reference)


class Charge(models.Model):
    """What the customer really paid or got back. The provider's books, not ours."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.UUIDField(unique=True)
    kind = models.CharField(max_length=10, choices=Kind, default=Kind.CHARGE)
    amount = models.BigIntegerField()
    scenario = models.CharField(max_length=20, choices=Scenario)
    decided_at = models.DateTimeField()

    def __str__(self) -> str:
        return str(self.reference)

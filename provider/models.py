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
    # Only its statement tells the truth about these.
    SILENT = "silent", "Moves the money, but never says so"
    LATE_SUCCESS = "late_success", "Declines, but moves the money anyway"
    WRONG_AMOUNT = "wrong_amount", "Moves 500 francs too much"
    MOVED_TWICE = "moved_twice", "Moves the money twice"
    NOT_IN_STATEMENT = "not_in_statement", "Leaves it out of its statement"

    @classmethod
    def recoverable(cls) -> set[str]:
        """Those that end well without reconciliation."""
        return set(cls.values) - {
            cls.SILENT,
            cls.LATE_SUCCESS,
            cls.WRONG_AMOUNT,
            cls.MOVED_TWICE,
            cls.NOT_IN_STATEMENT,
        }


class Kind(models.TextChoices):
    CHARGE = "charge"
    REFUND = "refund"
    # Money that should not have been taken, given back.
    RETURN = "return"


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

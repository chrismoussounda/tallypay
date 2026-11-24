import uuid
from typing import ClassVar

from django.db import models
from django.db.models import Q


class Task(models.Model):
    """Work to do later. Postgres remembers it; QStash only wakes the app."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=40)
    subject = models.UUIDField()
    attempt = models.PositiveIntegerField(default=1)
    due_at = models.DateTimeField()
    claimed_until = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes: ClassVar = [
            models.Index(
                fields=["due_at"], condition=Q(done_at=None), name="task_waiting"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.subject}"

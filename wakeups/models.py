from django.db import models


class WakeUp(models.Model):
    """A delayed call that came back, to show the hosting can retry work later."""

    id = models.UUIDField(primary_key=True)
    due_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.id} due {self.due_at:%Y-%m-%d %H:%M:%S}"

    @property
    def late_by(self) -> float:
        return (self.received_at - self.due_at).total_seconds()

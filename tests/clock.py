from datetime import datetime, timedelta

from django.utils import timezone

from tasks import queue
from tasks.models import Task


class Clock:
    def __init__(self) -> None:
        self.now = timezone.now()

    def __call__(self) -> datetime:
        return self.now

    def run_until_quiet(self) -> None:
        """Jumps from one due task to the next, until none is left."""
        for _ in range(500):
            waiting = Task.objects.filter(done_at=None).order_by("due_at").first()
            if waiting is None:
                return
            after_crash = waiting.claimed_until or waiting.due_at
            self.now = max(self.now, waiting.due_at, after_crash) + timedelta(
                microseconds=1
            )
            queue.run_due()
        raise AssertionError("never quiet")

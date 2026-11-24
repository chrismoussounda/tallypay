import logging
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import qstash

from .models import Task

log = logging.getLogger(__name__)

# Longer than a function may run, so that a lease only ends after a crash.
LEASE = timedelta(minutes=6)
# The clocks of QStash and Postgres are not exactly the same.
EARLY = timedelta(seconds=2)

Handler = Callable[[uuid.UUID, int], None]
_handlers: dict[str, Handler] = {}


def handles(kind: str) -> Callable[[Handler], Handler]:
    """Registers what does a kind of task. It may run more than once."""

    def register(handler: Handler) -> Handler:
        _handlers[kind] = handler
        return handler

    return register


def add(kind: str, subject: uuid.UUID, delay: timedelta, attempt: int = 1) -> Task:
    """Committed with whatever asks for it, so neither exists without the other."""
    task = Task.objects.create(
        kind=kind, subject=subject, attempt=attempt, due_at=timezone.now() + delay
    )
    transaction.on_commit(partial(_wake, task.id, delay))
    return task


def _wake(task_id: uuid.UUID, delay: timedelta) -> None:
    if not settings.QSTASH_TOKEN:
        return
    try:
        qstash.publish("/internal/tasks", {"task": str(task_id)}, delay)
    except Exception:
        # The task is kept, and found by `run_due`.
        log.exception("QStash was not told of task %s", task_id)


def run(task_id: uuid.UUID) -> bool:
    """Whether the task is done, now or before. Not if early or under way."""
    now = timezone.now()
    claimed = Task.objects.filter(
        Q(claimed_until=None) | Q(claimed_until__lt=now),
        id=task_id,
        done_at=None,
        due_at__lte=now + EARLY,
    ).update(claimed_until=now + LEASE)
    task = Task.objects.filter(id=task_id).first()
    if not claimed or task is None:
        return task is None or task.done_at is not None
    try:
        _handlers[task.kind](task.subject, task.attempt)
    except Exception:
        Task.objects.filter(id=task_id).update(claimed_until=None)
        raise
    Task.objects.filter(id=task_id).update(done_at=timezone.now())
    return True


def run_due(seconds: float = float("inf")) -> int:
    """Runs what is due, starting nothing after that long. How many were done."""
    started = time.monotonic()
    due = Task.objects.filter(done_at=None, due_at__lte=timezone.now())
    done = 0
    for task_id in list(due.order_by("due_at").values_list("id", flat=True)):
        if time.monotonic() - started >= seconds:
            break
        try:
            done += run(task_id)
        except Exception:
            log.exception("Task %s failed", task_id)
    return done

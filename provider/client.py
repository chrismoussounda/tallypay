"""How the app deals with the provider, whatever it is asked to do."""

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta

from tasks import queue
from tasks.models import Task

from . import simulator

log = logging.getLogger(__name__)

# When to look again, after each try. Then it is left to reconciliation.
RETRIES = [timedelta(seconds=seconds) for seconds in (10, 30, 120, 600, 3600, 6 * 3600)]


def process(
    task: str,
    subject: uuid.UUID,
    attempt: int,
    ask: Callable[[], None],
    settle: Callable[[uuid.UUID, bool], None],
) -> None:
    """One try to have something done by the provider, and to learn how it went."""
    # Before asking, so that a crash while asking cannot lose it.
    if attempt <= len(RETRIES):
        _look_again(task, subject, attempt)
    else:
        log.warning("%s %s is left to reconciliation", task, subject)
    if not simulator.knows(subject):
        try:
            ask()
        except simulator.NoAnswer:
            return
    paid = simulator.outcome(subject)
    if paid is not None:
        settle(subject, paid)


def _look_again(task: str, subject: uuid.UUID, attempt: int) -> None:
    planned = Task.objects.filter(kind=task, subject=subject, attempt=attempt + 1)
    # A try that crashed runs again, and must not plan a second one.
    if not planned.exists():
        queue.add(task, subject, RETRIES[attempt - 1], attempt + 1)

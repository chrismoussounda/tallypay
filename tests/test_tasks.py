import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import transaction
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import Settings

from config import qstash
from tasks import queue
from tasks.models import Task

from .conftest import Together
from .test_wakeups import CURRENT, NEXT, sign

NOW = timedelta()


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, int]]:
    seen: list[tuple[uuid.UUID, int]] = []
    monkeypatch.setitem(queue._handlers, "note", lambda s, a: seen.append((s, a)))
    return seen


def add(delay: timedelta = NOW, kind: str = "note", attempt: int = 1) -> Task:
    return queue.add(kind, uuid.uuid4(), delay, attempt)


@pytest.mark.django_db
def test_a_due_task_runs_once(ran: list[Any]) -> None:
    task = add(attempt=3)
    assert queue.run(task.id)
    assert queue.run(task.id)
    assert ran == [(task.subject, 3)]


@pytest.mark.django_db
def test_a_task_waits_until_it_is_due(
    ran: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    task = add(timedelta(minutes=1))
    assert not queue.run(task.id)
    assert queue.run_due() == 0
    later = timezone.now() + timedelta(minutes=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert queue.run_due() == 1
    assert len(ran) == 1


@pytest.mark.django_db
def test_a_task_that_failed_can_run_again(
    ran: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(subject: uuid.UUID, attempt: int) -> None:
        raise RuntimeError("crash")

    task = add()
    with monkeypatch.context() as patch:
        patch.setitem(queue._handlers, "note", broken)
        with pytest.raises(RuntimeError):
            queue.run(task.id)
    assert queue.run(task.id)
    assert len(ran) == 1


@pytest.mark.django_db
def test_a_failing_task_does_not_hold_the_others_back(ran: list[Any]) -> None:
    add(kind="unknown")
    add()
    assert queue.run_due() == 1


@pytest.mark.django_db
def test_a_task_lost_in_a_crash_is_taken_over_after_its_lease(
    ran: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    task = add()
    Task.objects.update(claimed_until=timezone.now() + queue.LEASE)
    assert not queue.run(task.id)
    later = timezone.now() + queue.LEASE * 2
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert queue.run(task.id)
    assert len(ran) == 1


def test_a_task_called_many_times_at_once_runs_once(
    together: Together, ran: list[Any]
) -> None:
    task = add()
    outcomes = together(8, lambda: queue.run(task.id))
    assert not [o for o in outcomes if isinstance(o, Exception)]
    assert len(ran) == 1


def test_qstash_is_told_only_once_committed(
    committed: None, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.QSTASH_TOKEN = "token"
    told: list[Any] = []
    monkeypatch.setattr(qstash, "publish", lambda *a: told.append(a))
    with pytest.raises(RuntimeError), transaction.atomic():
        add()
        raise RuntimeError("crash")
    assert told == []
    with transaction.atomic():
        task = add(timedelta(seconds=30))
        assert told == []
    assert told == [("/internal/tasks", {"task": str(task.id)}, timedelta(seconds=30))]


def test_a_task_survives_when_qstash_cannot_be_told(
    committed: None,
    settings: Settings,
    ran: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any) -> None:
        raise OSError("down")

    settings.QSTASH_TOKEN = "token"
    monkeypatch.setattr(qstash, "publish", broken)
    add()
    assert queue.run_due() == 1


@pytest.mark.django_db
def test_only_a_signed_call_runs_a_task(ran: list[Any], settings: Settings) -> None:
    settings.PUBLIC_URL = "https://tallypay.test"
    settings.QSTASH_CURRENT_SIGNING_KEY = CURRENT
    settings.QSTASH_NEXT_SIGNING_KEY = NEXT
    early, due = add(timedelta(hours=1)), add()

    def call(task: Any, **claims: Any) -> int:
        body = json.dumps({"task": task})
        claims = {"sub": "https://tallypay.test/internal/tasks"} | claims
        return (
            Client()
            .post(
                "/internal/tasks",
                body,
                content_type="application/json",
                headers={"Upstash-Signature": sign(body, **claims)},
            )
            .status_code
        )

    assert call(str(due.id), sub="https://tallypay.test/internal/wake") == 401
    assert call("no id") == 401
    assert call(5) == 401
    assert ran == []
    assert call(str(early.id)) == 409
    assert [call(str(due.id)) for _ in range(2)] == [204, 204]
    assert len(ran) == 1

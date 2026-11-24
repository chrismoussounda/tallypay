import threading
from collections.abc import Callable, Iterator

import pytest
from django.db import connection
from pytest_django import DjangoDbBlocker

TABLES = [
    "webhooks_try",
    "webhooks_delivery",
    "webhooks_event",
    "webhooks_endpoint",
    "tasks_task",
    "provider_request",
    "provider_charge",
    "idempotency_idempotencyrecord",
    "reconciliation_discrepancy",
    "reconciliation_statementline",
    "refunds_refund",
    "payments_paymentevent",
    "payments_payment",
    "merchants_apikey",
    "merchants_merchant",
    "ledger_line",
    "ledger_entry",
    "ledger_account",
]

Together = Callable[[int, Callable[[], object]], list[object]]


@pytest.fixture
def committed(
    django_db_setup: None, django_db_blocker: DjangoDbBlocker
) -> Iterator[None]:
    """Real commits, deleted afterwards past the append-only rules."""
    with django_db_blocker.unblock():
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                for table in TABLES:
                    cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
                    cursor.execute(f"DELETE FROM {table}")  # noqa: S608
                    cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
            connection.close()


@pytest.fixture
def together(committed: None) -> Together:
    """Runs the same thing in many threads at once, and returns each outcome."""

    def run(times: int, action: Callable[[], object]) -> list[object]:
        barrier = threading.Barrier(times)
        outcomes: list[object] = []

        def one() -> None:
            barrier.wait()
            try:
                outcomes.append(action())
            except Exception as error:
                outcomes.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=one) for _ in range(times)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return outcomes

    return run

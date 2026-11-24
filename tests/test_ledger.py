from collections.abc import Iterator
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from ledger import books
from ledger.models import Account, Entry, Line

pytestmark = pytest.mark.django_db
CODES = ["cash", "fees", "merchant", "provider"]


def accounts() -> list[Account]:
    return [
        Account.objects.create(code=code, name=code, kind=Account.Kind.ASSET)
        for code in CODES
    ]


def check_now() -> None:
    """What Postgres checks at the end of a transaction, without ending it."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED")


def refused() -> Any:
    return pytest.raises(IntegrityError)


def insert(amounts: dict[Account, int], reverses: Entry | None = None) -> Entry:
    """Past the checks of `books`, to face those of Postgres."""
    entry = Entry.objects.create(description="raw", reverses=reverses)
    for account, amount in amounts.items():
        Line.objects.create(entry=entry, account=account, amount=amount)
    return entry


def test_an_entry_moves_money_and_the_books_balance() -> None:
    cash, fees, merchant, _ = accounts()
    books.post("Sale", {cash: 10_000, merchant: -9_800, fees: -200})
    check_now()
    assert books.balance(cash) == 10_000
    assert books.trial_balance() == {
        "cash": 10_000,
        "fees": -200,
        "merchant": -9_800,
        "provider": 0,
    }


@pytest.mark.parametrize(
    "amounts", [[], [5], [5, -4], [0, 0], [5, 0, -5], [0.5, -0.5], [True, -1]]
)
def test_an_entry_that_makes_no_sense_is_not_written(amounts: list[Any]) -> None:
    with pytest.raises(books.InvalidEntry):
        books.post("Bad", dict(zip(accounts(), amounts, strict=False)))
    assert not Entry.objects.exists()


@pytest.mark.parametrize("amounts", [[], [5], [5, -4], [5, 5, -9]])
def test_postgres_refuses_an_entry_that_does_not_balance(amounts: list[int]) -> None:
    known = accounts()
    with refused(), transaction.atomic():
        insert(dict(zip(known, amounts, strict=False)))
        check_now()


def test_postgres_refuses_a_line_added_after_the_check() -> None:
    cash, fees, merchant, _ = accounts()
    entry = insert({cash: 5, fees: -5})
    check_now()
    with refused(), transaction.atomic():
        Line.objects.create(entry=entry, account=merchant, amount=1)
        check_now()


def test_postgres_refuses_a_line_of_nothing_or_an_account_twice() -> None:
    cash, fees, merchant, _ = accounts()
    entry = insert({cash: 5, fees: -5})
    for account, amount in [(merchant, 0), (cash, 5)]:
        with refused(), transaction.atomic():
            Line.objects.create(entry=entry, account=account, amount=amount)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE ledger_line SET amount = amount * 2",
        "UPDATE ledger_entry SET description = 'x'",
        "DELETE FROM ledger_line",
        "DELETE FROM ledger_entry",
        "TRUNCATE ledger_line",
        "TRUNCATE ledger_entry CASCADE",
        "UPDATE ledger_account SET kind = 'income'",
        "UPDATE ledger_account SET code = code || '2'",
    ],
)
def test_postgres_refuses_to_change_the_books(sql: str) -> None:
    cash, fees, *_ = accounts()
    books.post("Sale", {cash: 5, fees: -5})
    check_now()
    with refused(), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql)


def test_the_name_of_an_account_may_be_corrected() -> None:
    cash, *_ = accounts()
    cash.name = "Cash in hand"
    cash.save()


def test_a_reversal_undoes_an_entry_once() -> None:
    cash, fees, *_ = accounts()
    sale = books.post("Sale", {cash: 5, fees: -5})
    books.reverse(sale, "Mistake")
    check_now()
    assert set(books.trial_balance().values()) == {0}
    with refused():
        books.reverse(sale, "Again")


def test_postgres_refuses_a_reversal_that_is_not_the_opposite() -> None:
    cash, fees, merchant, _ = accounts()
    for wrong in ({cash: -4, fees: 4}, {cash: 5, fees: -5}, {merchant: -5, fees: 5}):
        sale = books.post("Sale", {cash: 5, fees: -5})
        check_now()
        with refused(), transaction.atomic():
            insert(wrong, reverses=sale)
            check_now()


@pytest.fixture
def other() -> Iterator[BaseDatabaseWrapper]:
    """A connection that really commits, unlike the test's own."""
    other = connection.copy()
    try:
        yield other
    finally:
        other.rollback()
        with other.cursor() as cursor:
            tables = ["ledger_line", "ledger_entry", "ledger_account"]
            for table in tables:
                cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
                cursor.execute(f"DELETE FROM {table}")  # noqa: S608
                cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
        other.close()


ENTRY = "00000000-0000-4000-8000-000000000001"


def write(other: BaseDatabaseWrapper, amounts: list[int]) -> None:
    with other.cursor() as cursor:
        cursor.execute(
            "INSERT INTO ledger_account (code, name, kind)"
            " VALUES ('a', 'A', 'asset'), ('b', 'B', 'asset'),"
            " ('c', 'C', 'asset'), ('d', 'D', 'asset')"
        )
        cursor.execute(
            "INSERT INTO ledger_entry (id, description, created_at, transaction_id)"
            " VALUES (%s, 'raw', now(), 1)",
            [ENTRY],
        )
        add_lines(cursor, amounts, "ab")


def add_lines(cursor: Any, amounts: list[int], codes: str) -> None:
    for code, amount in zip(codes, amounts, strict=True):
        cursor.execute(
            "INSERT INTO ledger_line (entry_id, account_id, amount)"
            " SELECT %s, id, %s FROM ledger_account WHERE code = %s",
            [ENTRY, amount, code],
        )


def count(other: BaseDatabaseWrapper) -> int:
    with other.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM ledger_line")
        return int(cursor.fetchone()[0])


def test_an_unbalanced_entry_cannot_be_committed(other: BaseDatabaseWrapper) -> None:
    other.set_autocommit(False)
    write(other, [5, -4])
    with refused():
        other.commit()
    other.rollback()
    other.set_autocommit(True)
    assert count(other) == 0


def test_nothing_joins_an_entry_once_committed(other: BaseDatabaseWrapper) -> None:
    other.set_autocommit(False)
    write(other, [5, -5])
    other.commit()
    with refused(), other.cursor() as cursor:
        add_lines(cursor, [7, -7], "cd")
    other.rollback()
    other.set_autocommit(True)
    assert count(other) == 2


AMOUNTS = st.integers(min_value=-(2**53), max_value=2**53)
STEPS = st.lists(
    st.one_of(
        st.tuples(st.just("post"), st.lists(AMOUNTS, min_size=4, max_size=4)),
        st.tuples(st.just("raw"), st.lists(AMOUNTS, min_size=1, max_size=4)),
        st.tuples(st.just("reverse"), st.integers(min_value=0, max_value=50)),
    ),
    max_size=20,
)


class AnySequence(TestCase):
    @given(STEPS)
    @settings(max_examples=200, deadline=None)
    def test_leaves_the_books_balanced(self, steps: list[tuple[str, Any]]) -> None:
        """Whatever is tried, the books match a plain sum of what was accepted."""
        known = accounts()
        expected = dict.fromkeys(CODES, 0)
        entries: list[tuple[Entry, dict[Account, int]]] = []
        done: set[int] = set()
        for step, value in steps:
            if step == "post":
                value[-1] = -sum(value[:-1])
                moved = {a: n for a, n in zip(known, value, strict=True) if n}
                if not moved:
                    continue
                entries.append((books.post("Any", moved), moved))
            elif step == "raw":
                moved = {a: n for a, n in zip(known, value, strict=False) if n}
                if moved and sum(moved.values()) == 0:
                    continue
                with refused(), transaction.atomic():
                    insert(moved)
                    check_now()
                continue
            else:
                if not entries:
                    continue
                at = value % len(entries)
                if at in done:
                    with refused():
                        books.reverse(entries[at][0], "Again")
                    continue
                done.add(at)
                moved = {a: -n for a, n in entries[at][1].items()}
                entries.append((books.reverse(entries[at][0], "Undo"), moved))
            check_now()
            for account, amount in moved.items():
                expected[account.code] += amount
        assert books.trial_balance() == expected
        assert sum(expected.values()) == 0

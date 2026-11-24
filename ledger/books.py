from collections.abc import Mapping

from django.db import transaction
from django.db.models import Sum

from .models import Account, Entry, Line


class InvalidEntry(ValueError):
    pass


def post(
    description: str, amounts: Mapping[Account, int], *, reverses: Entry | None = None
) -> Entry:
    """Records an entry: debits positive, credits negative, in whole francs."""
    if any(type(amount) is not int for amount in amounts.values()):
        raise InvalidEntry("Amounts are whole francs.")
    if len(amounts) < 2 or 0 in amounts.values():
        raise InvalidEntry("An entry moves money between at least two accounts.")
    if sum(amounts.values()) != 0:
        raise InvalidEntry("Debits and credits differ.")
    with transaction.atomic():
        entry = Entry.objects.create(description=description, reverses=reverses)
        Line.objects.bulk_create(
            Line(entry=entry, account=account, amount=amount)
            for account, amount in amounts.items()
        )
    return entry


def reverse(entry: Entry, description: str) -> Entry:
    """Undoes an entry with its opposite. Postgres refuses a second reversal."""
    lines = entry.lines.select_related("account")
    return post(
        description, {line.account: -line.amount for line in lines}, reverses=entry
    )


def balance(account: Account) -> int:
    """Debits less credits."""
    total: int | None = account.lines.aggregate(total=Sum("amount"))["total"]
    return int(total or 0)


def trial_balance() -> dict[str, int]:
    """Every account's balance, which add up to zero."""
    totals = Account.objects.annotate(total=Sum("lines__amount")).order_by("code")
    return {account.code: int(account.total or 0) for account in totals}

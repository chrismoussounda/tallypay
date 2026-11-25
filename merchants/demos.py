"""What a demo may do, so that its visitors cannot use up what is free."""

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from provider.models import Scenario

from .models import Demo, DemoDay, Merchant

# Of all demos. Each may take 13 of the 1,000 messages a day of QStash.
PER_DAY = 50
DAY = timedelta(days=1)
# The second charge leads to no shop, so nobody in the demo would see it.
NOT_HERE = {Scenario.MOVED_TWICE}
LOCK = 7_301


class UsedUp(Exception):
    pass


class NotInDemo(Exception):
    """A scenario that a demo cannot show."""


def lock() -> None:
    """One demo at a time, until the transaction ends."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK])


@transaction.atomic
def spend(merchant: Merchant, scenario: str = Scenario.NORMAL) -> None:
    """Call it before locking any row of the merchant."""
    if not Demo.objects.filter(merchant=merchant).exists():
        return
    if scenario in NOT_HERE:
        raise NotInDemo
    lock()
    demo = Demo.objects.get(merchant=merchant)
    today, _ = DemoDay.objects.get_or_create(day=timezone.now().date())
    if demo.over or not demo.left or today.used >= PER_DAY:
        raise UsedUp
    demo.used += 1
    demo.save(update_fields=["used"])
    today.used += 1
    today.save(update_fields=["used"])

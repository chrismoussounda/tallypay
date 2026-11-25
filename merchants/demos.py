"""What a demo may do, so that its visitors cannot use up what is free."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Demo, Merchant

# Of all the demos opened in a day, for the 1,000 messages a day of QStash.
PER_DAY = 80
DAY = timedelta(days=1)
LOCK = 7_301


class UsedUp(Exception):
    pass


def lock() -> None:
    """One demo at a time, until the transaction ends."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK])


@transaction.atomic
def spend(merchant: Merchant) -> None:
    if not Demo.objects.filter(merchant=merchant).exists():
        return
    lock()
    demo = Demo.objects.get(merchant=merchant)
    recent = Demo.objects.filter(created_at__gt=timezone.now() - DAY)
    today = recent.aggregate(total=Sum("used"))["total"] or 0
    if demo.closed_at or not demo.left or today >= PER_DAY:
        raise UsedUp
    demo.used += 1
    demo.save(update_fields=["used"])

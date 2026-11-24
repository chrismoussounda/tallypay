from typing import Any

from django.core.management.base import BaseCommand

from reconciliation import resolving
from reconciliation.models import Discrepancy


class Command(BaseCommand):
    help = "Lists the discrepancies. They are resolved from the dashboard."

    def handle(self, *args: Any, **options: Any) -> None:
        waiting = Discrepancy.objects.exclude(status=Discrepancy.Status.RESOLVED)
        for found in waiting.order_by("created_at"):
            actions = "/".join(sorted(resolving.allowed(found)))
            self.stdout.write(
                f"{found.id} {found.kind} {found.amount:+} {found.status} {actions}"
            )

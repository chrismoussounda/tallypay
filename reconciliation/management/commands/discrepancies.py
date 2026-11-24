import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from reconciliation import resolving
from reconciliation.models import Discrepancy


class Command(BaseCommand):
    help = "Lists the discrepancies, or has one resolved by two people."

    def add_arguments(self, parser: CommandParser) -> None:
        step = parser.add_subparsers(dest="step")
        propose = step.add_parser("propose")
        propose.add_argument("id", type=uuid.UUID)
        propose.add_argument("action")
        propose.add_argument("--by", required=True)
        propose.add_argument("--note", required=True)
        for name in ("approve", "reject"):
            other = step.add_parser(name)
            other.add_argument("id", type=uuid.UUID)
            other.add_argument("--by", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            match options["step"]:
                case "propose":
                    found = resolving.propose(
                        options["id"],
                        options["action"],
                        options["by"],
                        options["note"],
                    )
                case "approve":
                    found = resolving.approve(options["id"], options["by"])
                case "reject":
                    found = resolving.reject(options["id"], options["by"])
                case _:
                    self._list()
                    return
        except resolving.NotPossible as error:
            raise CommandError(str(error)) from error
        except Discrepancy.DoesNotExist as error:
            raise CommandError("No such discrepancy.") from error
        self.stdout.write(f"{found.id} {found.status}")

    def _list(self) -> None:
        waiting = Discrepancy.objects.exclude(status=Discrepancy.Status.RESOLVED)
        for found in waiting.order_by("created_at"):
            actions = "/".join(sorted(resolving.allowed(found)))
            self.stdout.write(
                f"{found.id} {found.kind} {found.amount:+} {found.status} {actions}"
            )

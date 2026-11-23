from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from wakeups import qstash


class Command(BaseCommand):
    help = "Asks QStash to call PUBLIC_URL back after a delay."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--delay", type=int, default=60, help="in seconds")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["delay"] < 1:
            raise CommandError("The delay must be at least 1 second.")
        wakeup_id = qstash.schedule(timedelta(seconds=options["delay"]))
        self.stdout.write(f"{wakeup_id} will call {qstash.wake_url()}")

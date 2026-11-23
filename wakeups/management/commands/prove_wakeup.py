from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from wakeups import qstash


class Command(BaseCommand):
    help = "Asks QStash to call PUBLIC_URL back after a delay."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--delay", type=int, default=60, help="in seconds")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["delay"] < 1:
            raise CommandError("The delay must be at least 1 second.")
        if not settings.QSTASH_TOKEN:
            raise CommandError("QSTASH_TOKEN is not set.")
        if urlsplit(settings.PUBLIC_URL).hostname in ("localhost", "127.0.0.1"):
            raise CommandError(
                "QStash cannot reach this computer: set PUBLIC_URL to the live address."
            )
        wakeup_id = qstash.schedule(timedelta(seconds=options["delay"]))
        self.stdout.write(f"{wakeup_id} will call {qstash.wake_url()}")

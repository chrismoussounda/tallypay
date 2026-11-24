import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from tasks import queue


class Command(BaseCommand):
    help = (
        "Runs the tasks that are due. With --watch, keeps on: for use without QStash."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        while True:
            done = queue.run_due()
            if done or not options["watch"]:
                self.stdout.write(f"{done} done")
            if not options["watch"]:
                return
            time.sleep(1)

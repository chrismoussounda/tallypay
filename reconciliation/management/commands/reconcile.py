from typing import Any

from django.core.management.base import BaseCommand

from reconciliation import matching


class Command(BaseCommand):
    help = "Compares the provider's statement with the books."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(f"{matching.run()} waiting for someone")

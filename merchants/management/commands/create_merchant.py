from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from merchants import keys


class Command(BaseCommand):
    help = "Creates a merchant and shows its API key, once."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name")

    def handle(self, *args: Any, **options: Any) -> None:
        merchant = keys.create_merchant(options["name"])
        self.stdout.write(f"{merchant.id}\n{keys.issue(merchant)}")

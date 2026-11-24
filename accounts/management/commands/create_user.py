import getpass
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.models import User


class Command(BaseCommand):
    help = "Creates a user, and asks for their password."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("email")
        parser.add_argument("name")
        parser.add_argument("role", choices=User.Role.values)
        parser.add_argument("--merchant", help="The merchant's id")

    def handle(self, *args: Any, **options: Any) -> None:
        user = User(
            email=options["email"].strip().lower(),
            name=options["name"],
            role=options["role"],
            merchant_id=options["merchant"],
        )
        if user.is_operator == bool(user.merchant_id):
            raise CommandError("Everyone but an operator belongs to a merchant.")
        password = getpass.getpass("Password: ")
        try:
            validate_password(password, user)
            user.set_password(password)
            user.full_clean()
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        user.save()
        self.stdout.write(f"{user.email} can sign in.")

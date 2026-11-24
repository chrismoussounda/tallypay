from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "payments"

    def ready(self) -> None:
        from . import processing  # noqa: F401

from django.apps import AppConfig


class RefundsConfig(AppConfig):
    name = "refunds"

    def ready(self) -> None:
        from . import processing  # noqa: F401

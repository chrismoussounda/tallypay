from django.apps import AppConfig


class DemosConfig(AppConfig):
    name = "demos"

    def ready(self) -> None:
        # Registers what closes a demo.
        from . import opening  # noqa: F401

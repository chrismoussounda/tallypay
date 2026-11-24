from django.apps import AppConfig


class ReconciliationConfig(AppConfig):
    name = "reconciliation"

    def ready(self) -> None:
        from . import resolving  # noqa: F401

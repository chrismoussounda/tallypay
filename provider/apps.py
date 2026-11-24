from django.apps import AppConfig


class ProviderConfig(AppConfig):
    name = "provider"

    def ready(self) -> None:
        from . import simulator  # noqa: F401

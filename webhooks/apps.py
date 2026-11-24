from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    name = "webhooks"

    def ready(self) -> None:
        from . import service  # noqa: F401

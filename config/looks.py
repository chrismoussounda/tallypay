"""How the site speaks until the visitor says otherwise: in French."""

from django.conf import settings
from django.http import HttpRequest
from django.middleware.locale import LocaleMiddleware
from django.utils import translation

class FrenchUntilChosen(LocaleMiddleware):
    """The language chosen with the switch, else French, whatever the browser asks."""

    def process_request(self, request: HttpRequest) -> None:
        chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        known = dict(settings.LANGUAGES)
        translation.activate(chosen if chosen in known else settings.LANGUAGE_CODE)
        request.LANGUAGE_CODE = translation.get_language()

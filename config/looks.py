"""How the site looks and speaks until the visitor says otherwise: light, in French."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.locale import LocaleMiddleware
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

THEME_COOKIE = "theme"
YEAR = 365 * 24 * 3600


class FrenchUntilChosen(LocaleMiddleware):
    """The language chosen with the switch, else French, whatever the browser asks."""

    def process_request(self, request: HttpRequest) -> None:
        chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        known = dict(settings.LANGUAGES)
        translation.activate(chosen if chosen in known else settings.LANGUAGE_CODE)
        request.LANGUAGE_CODE = translation.get_language()


def theme(request: HttpRequest) -> dict[str, Any]:
    """Light unless the visitor asked for dark."""
    return {"theme": "dark" if request.COOKIES.get(THEME_COOKIE) == "dark" else "light"}


@require_POST
def set_theme(request: HttpRequest) -> HttpResponse:
    """A form, like the language switch, so it works without JavaScript."""
    going = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(
        going, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        going = "/"
    answer = HttpResponseRedirect(going)
    dark = request.POST.get("theme") == "dark"
    answer.set_cookie(
        THEME_COOKIE,
        "dark" if dark else "light",
        max_age=YEAR,
        samesite="Lax",
        secure=request.is_secure(),
        httponly=True,
    )
    return answer

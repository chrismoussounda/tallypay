import time
from collections.abc import Callable

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse

MAX_SESSION = 12 * 3600
STARTED = "started"


class SessionLifetime:
    """Signs everyone out 12 hours after they signed in, however active."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            request.user.is_authenticated
            and time.time() - request.session.get(STARTED, 0) > MAX_SESSION
        ):
            logout(request)
        return self.get_response(request)

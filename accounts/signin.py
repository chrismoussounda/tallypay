"""Passwords, with a limit on how many can be tried."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.http import HttpRequest
from django.utils import timezone

from .models import Attempt, User

WINDOW = timedelta(minutes=15)
# Counted with the address too, so that a stranger cannot easily lock someone out.
PER_EMAIL_AND_ADDRESS = 5
PER_EMAIL = 50
PER_ADDRESS = 30


class TooMany(Exception):
    pass


def address_of(request: HttpRequest) -> str:
    # Vercel sets this header itself. Elsewhere, anyone could.
    given = request.headers.get("X-Real-IP", "") if settings.ON_VERCEL else ""
    return str(given or request.META.get("REMOTE_ADDR", ""))[:45]


def check(request: HttpRequest, email: str, password: str) -> User | None:
    email, address = email.strip().lower()[:254], address_of(request)
    # Written down first, so that tries at the same time all count.
    attempt = Attempt.objects.create(email=email, address=address)
    recent = Attempt.objects.filter(created_at__gt=timezone.now() - WINDOW)
    if (
        recent.filter(email=email, address=address).count() > PER_EMAIL_AND_ADDRESS
        or recent.filter(email=email).count() > PER_EMAIL
        or recent.filter(address=address).count() > PER_ADDRESS
    ):
        raise TooMany
    user = authenticate(request, username=email, password=password)
    if not isinstance(user, User):
        return None
    attempt.delete()
    return user

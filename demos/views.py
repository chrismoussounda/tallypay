import hmac
import uuid

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from accounts.models import User
from accounts.signin import address_of
from dashboard.demo import enter
from merchants.models import Demo
from webhooks import signing

from . import opening

WHO = {"owner", "proposer"}
# Seconds, as Standard Webhooks advises against replays.
TOLERANCE = 300


@require_safe
@never_cache
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html")


@require_safe
@never_cache
def about(request: HttpRequest) -> HttpResponse:
    language = "fr" if get_language() == "fr" else "en"
    return render(request, f"about_{language}.html")


@require_POST
def open_demo(request: HttpRequest) -> HttpResponse:
    who = request.POST.get("who", "")
    if who not in WHO:
        return HttpResponse(status=400)
    if request.user.is_authenticated and _mine(request) is None:
        messages.error(request, _("You are signed in. Sign out to try the demo."))
        return redirect("home")
    try:
        demo = _mine(request) or opening.open_one(address_of(request))
    except opening.TooMany:
        messages.error(request, _("You opened several demos. Try again in an hour."))
        return redirect("home")
    except opening.Full:
        messages.error(request, _("All of today's demos are open. Try tomorrow."))
        return redirect("home")
    enter(request, demo.people.get(email__startswith=f"{who}-"))
    return redirect("start")


def _mine(request: HttpRequest) -> Demo | None:
    """The one the visitor is in. It is open, as nobody stays in a closed one."""
    user = request.user
    return user.demo if isinstance(user, User) else None


@csrf_exempt
@require_POST
def inbox(request: HttpRequest, merchant_id: uuid.UUID) -> HttpResponse:
    """A merchant's server, which checks the signature and keeps nothing."""
    demo = get_object_or_404(Demo, merchant_id=merchant_id)
    if demo.over:
        raise Http404
    if not demo.inbox_works:
        return HttpResponse(status=503)
    return HttpResponse(status=204 if _signed(request, demo) else 401)


def _signed(request: HttpRequest, demo: Demo) -> bool:
    given = request.headers.get("Webhook-Signature", "")
    try:
        message = uuid.UUID(request.headers.get("Webhook-Id", ""))
        sent = int(request.headers.get("Webhook-Timestamp", ""))
    except ValueError:
        return False
    if abs(int(timezone.now().timestamp()) - sent) > TOLERANCE:
        return False
    for endpoint in demo.merchant.endpoints.filter(removed_at=None):
        secret = signing.secret_of(endpoint.id)
        wanted = signing.headers(secret, message, sent, request.body)
        if hmac.compare_digest(given.encode(), wanted["Webhook-Signature"].encode()):
            return True
    return False

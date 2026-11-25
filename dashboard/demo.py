"""What only the visitor of a demo can do."""

import time

from django.contrib import messages
from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.middleware import STARTED
from accounts.models import User
from merchants.models import Demo
from reconciliation import matching

from .core import demo_page, user_of

BACKEND = "django.contrib.auth.backends.ModelBackend"


def enter(request: HttpRequest, user: User) -> None:
    """Without a password, which nobody in a demo has."""
    current = request.user
    same = isinstance(current, User) and current.demo_id == user.demo_id
    # Becoming someone else does not make the visit longer.
    started = request.session.get(STARTED) if same else None
    login(request, user, backend=BACKEND)
    request.session[STARTED] = started or time.time()


@demo_page
@require_POST
def become(request: HttpRequest) -> HttpResponse:
    person = get_object_or_404(
        User,
        demo=user_of(request).demo,
        is_active=True,
        email__startswith=f"{request.POST.get('who', '')[:20]}-",
    )
    enter(request, person)
    return redirect("start")


@demo_page
@require_POST
def reconcile(request: HttpRequest) -> HttpResponse:
    matching.run(_demo(request).merchant)
    messages.success(request, _("The statement was compared with the books."))
    return redirect("start")


@demo_page
@require_POST
def switch_inbox(request: HttpRequest) -> HttpResponse:
    demo = _demo(request)
    demo.inbox_works = request.POST.get("works") == "1"
    demo.save(update_fields=["inbox_works"])
    return redirect("webhooks")


def _demo(request: HttpRequest) -> Demo:
    demo = user_of(request).demo
    assert demo is not None  # noqa: S101
    return demo

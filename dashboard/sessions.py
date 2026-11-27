import time

from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from accounts import signin
from accounts.middleware import STARTED
from accounts.models import User

from .core import any_page, record, user_of
from .forms import Password, SignIn


@never_cache
def sign_in(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("start")
    form = SignIn(request.POST or None)
    status = 200
    if request.method == "POST":
        status = 422
        if form.is_valid():
            email, password = form.cleaned_data["email"], form.cleaned_data["password"]
            try:
                user = signin.check(request, email, password)
            except signin.TooMany:
                form.add_error(None, _("Too many tries. Try again in 15 minutes."))
            else:
                if user:
                    login(request, user)
                    request.session[STARTED] = time.time()
                    record(request, "signed_in", signin.address_of(request))
                    return redirect(_next(request))
                form.add_error(None, _("The email or the password is not right."))
    context = {"form": form, "next": request.GET.get("next", "")}
    return render(request, "dashboard/sign_in.html", context, status=status)


def _next(request: HttpRequest) -> str:
    wanted = request.GET.get("next", "")
    safe = url_has_allowed_host_and_scheme(
        wanted, {request.get_host()}, request.is_secure()
    )
    return wanted if safe else "start"


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    demo = isinstance(request.user, User) and request.user.demo_id
    logout(request)
    return redirect("home" if demo else "sign-in")


@require_safe
@any_page
def start(request: HttpRequest) -> HttpResponse:
    return redirect("discrepancies" if user_of(request).is_operator else "overview")


def confirmed(request: HttpRequest, form: Password) -> bool:
    """Whether the form is right, the password of the user included."""
    if not form.is_valid():
        return False
    if user_of(request).demo_id:
        return True
    try:
        user = signin.check(
            request, user_of(request).email, form.cleaned_data["password"]
        )
    except signin.TooMany:
        form.add_error("password", _("Too many tries. Try again in 15 minutes."))
        return False
    if user != request.user:
        form.add_error("password", _("The password is not right."))
        return False
    return True


@require_safe
@any_page
def docs(request: HttpRequest) -> HttpResponse:
    """The write-up, inside the app, so nobody has to leave it to understand it."""
    language = "fr" if get_language() == "fr" else "en"
    return render(
        request,
        "dashboard/docs.html",
        {"write_up": f"write_up_{language}.html", "in_app": True},
    )

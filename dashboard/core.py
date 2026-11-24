import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Model, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from accounts.models import Action, User
from merchants.models import Merchant

View = Callable[..., HttpResponse]
PAGE = 20


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def go(request: HttpRequest, name: str, *args: Any) -> HttpResponse:
    """To another page, whether the form was in a sheet or on a page of its own."""
    url = reverse(name, args=args)
    if not is_htmx(request):
        return redirect(url)
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def done(request: HttpRequest, text: str, name: str, *args: Any) -> HttpResponse:
    messages.success(request, text)
    return go(request, name, *args)


def _guard(allows: Callable[[User], bool]) -> Callable[[View], View]:
    def decorate(view: View) -> View:
        @never_cache
        @wraps(view)
        def guarded(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not request.user.is_authenticated:
                if is_htmx(request):
                    return go(request, "sign-in")
                return redirect_to_login(request.get_full_path())
            if not allows(user_of(request)):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return guarded

    return decorate


merchant_page = _guard(lambda user: user.merchant_id is not None)
owner_page = _guard(lambda user: user.role == User.Role.OWNER)
refunds_page = _guard(lambda user: user.can_refund)
developer_page = _guard(lambda user: user.can_develop)
operator_page = _guard(lambda user: user.is_operator)
any_page = _guard(lambda user: True)


def user_of(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # noqa: S101
    return request.user


def merchant_of(request: HttpRequest) -> Merchant:
    merchant = user_of(request).merchant
    if merchant is None:
        raise PermissionDenied
    return merchant


def sheet(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    """A form in a sheet, or on a page of its own without JavaScript."""
    if is_htmx(request):
        return render(request, template, context, status=status)
    context = context | {"sheet": template}
    return render(request, "dashboard/sheet_page.html", context, status=status)


def record(request: HttpRequest, what: str, subject: object) -> None:
    Action.objects.create(user=user_of(request), what=what, subject=str(subject)[:80])


def page_of[M: Model](
    request: HttpRequest, found: "QuerySet[M]"
) -> tuple[list[M], M | None]:
    """Newest first, and the last one when there is more."""
    after = request.GET.get("after", "")
    if after:
        try:
            last: Any = get_object_or_404(found, id=uuid.UUID(after))
        except ValueError:
            last = None
        if last is not None:
            found = found.filter(
                Q(created_at__lt=last.created_at)
                | Q(created_at=last.created_at, id__lt=last.id)
            )
    page = list(found.order_by("-created_at", "-id")[: PAGE + 1])
    return page[:PAGE], page[PAGE - 1] if len(page) > PAGE else None


def rows(request: HttpRequest, template: str, context: dict[str, Any]) -> HttpResponse:
    """The whole page, or only the rows that follow."""
    part = "#more" if is_htmx(request) and request.GET.get("after") else ""
    return render(request, template + part, context)

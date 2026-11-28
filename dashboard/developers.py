import uuid

from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from accounts.models import Action
from merchants import demos, keys
from merchants.models import ApiKey
from webhooks import sender, service, signing
from webhooks.models import Delivery, Endpoint

from .core import (
    DEMO_USED_UP,
    developer_page,
    done,
    merchant_of,
    merchant_page,
    owner_page,
    page_of,
    record,
    rows,
    sheet,
    user_of,
)
from .forms import NewEndpoint, NewKey, Password
from .sessions import confirmed


@require_safe
@merchant_page
def key_list(request: HttpRequest) -> HttpResponse:
    found = merchant_of(request).keys.order_by("revoked_at", "-created_at", "-id")
    return render(request, "dashboard/keys.html", {"keys": found})


@require_http_methods(["GET", "POST"])
@developer_page
def new_key(request: HttpRequest) -> HttpResponse:
    form = NewKey(request.POST or None)
    form.for_user(user_of(request))
    if request.method == "POST" and confirmed(request, form):
        with transaction.atomic():
            key = keys.issue(merchant_of(request), form.cleaned_data["name"])
            record(request, "key_created", form.cleaned_data["name"])
        context = {"value": key, "back": "keys"}
        return sheet(request, "dashboard/key_shown.html", context)
    status = 422 if form.errors else 200
    return sheet(request, "dashboard/new_key.html", {"form": form}, status)


@require_http_methods(["GET", "POST"])
@developer_page
def revoke_key(request: HttpRequest, key_id: int) -> HttpResponse:
    key = get_object_or_404(
        ApiKey, merchant=merchant_of(request), id=key_id, revoked_at=None
    )
    if request.method == "POST":
        with transaction.atomic():
            keys.revoke(key)
            record(request, "key_revoked", f"{key.name} {key}".strip())
        return done(request, _("Key revoked."), "keys")
    return sheet(request, "dashboard/revoke_key.html", {"key": key})


@require_safe
@merchant_page
def webhooks(request: HttpRequest) -> HttpResponse:
    merchant = merchant_of(request)
    found = Delivery.objects.filter(endpoint__merchant=merchant)
    status = request.GET.get("status", "")
    if status in Delivery.Status.values:
        found = found.filter(status=status)
    page, last = page_of(request, found.select_related("event", "endpoint"))
    context = {
        "endpoints": merchant.endpoints.filter(removed_at=None).order_by(
            "created_at", "id"
        ),
        "most": service.MAX_ENDPOINTS,
        "deliveries": page,
        "last": last,
        "status": status,
        "statuses": Delivery.Status.values,
    }
    return rows(request, "dashboard/webhooks.html", context)


@require_http_methods(["GET", "POST"])
@developer_page
def new_endpoint(request: HttpRequest) -> HttpResponse:
    form = NewEndpoint(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                endpoint = service.add_endpoint(
                    merchant_of(request), form.cleaned_data["url"]
                )
                record(request, "endpoint_added", endpoint.url)
        except sender.Refused as refused:
            form.add_error("url", _(str(refused)))
        except service.TooMany:
            form.add_error(None, _("You have reached the address limit."))
        else:
            context = {"value": signing.secret_of(endpoint.id), "back": "webhooks"}
            return sheet(request, "dashboard/secret_shown.html", context)
    status = 422 if form.errors else 200
    return sheet(request, "dashboard/new_endpoint.html", {"form": form}, status)


def _endpoint(request: HttpRequest, endpoint_id: uuid.UUID) -> Endpoint:
    return get_object_or_404(
        Endpoint, merchant=merchant_of(request), id=endpoint_id, removed_at=None
    )


@require_http_methods(["GET", "POST"])
@developer_page
def endpoint_secret(request: HttpRequest, endpoint_id: uuid.UUID) -> HttpResponse:
    endpoint = _endpoint(request, endpoint_id)
    form = Password(request.POST or None)
    form.for_user(user_of(request))
    if request.method == "POST" and confirmed(request, form):
        record(request, "secret_shown", endpoint.url)
        context = {"value": signing.secret_of(endpoint.id), "back": "webhooks"}
        return sheet(request, "dashboard/secret_shown.html", context)
    status = 422 if form.errors else 200
    asked = {"form": form, "endpoint": endpoint}
    return sheet(request, "dashboard/endpoint_secret.html", asked, status)


@require_http_methods(["GET", "POST"])
@developer_page
def remove_endpoint(request: HttpRequest, endpoint_id: uuid.UUID) -> HttpResponse:
    endpoint = _endpoint(request, endpoint_id)
    if request.method == "POST":
        with transaction.atomic():
            service.remove_endpoint(endpoint)
            record(request, "endpoint_removed", endpoint.url)
        return done(request, _("Address removed."), "webhooks")
    return sheet(request, "dashboard/remove_endpoint.html", {"endpoint": endpoint})


@require_http_methods(["GET", "POST"])
@merchant_page
def delivery_detail(request: HttpRequest, delivery_id: uuid.UUID) -> HttpResponse:
    delivery = get_object_or_404(
        Delivery.objects.select_related("event", "endpoint"),
        endpoint__merchant=merchant_of(request),
        id=delivery_id,
    )
    error = ""
    if request.method == "POST" and user_of(request).can_develop:
        try:
            with transaction.atomic():
                service.replay(delivery.id)
                record(request, "delivery_replayed", delivery.event.type)
        except service.UnderWay:
            error = _("It is still being sent.")
        except demos.UsedUp:
            error = str(DEMO_USED_UP)
        except service.Removed:
            error = _("This address was removed.")
        else:
            return done(request, _("Sent again."), "delivery", delivery.id)
    context = {
        "delivery": delivery,
        "history": delivery.history.order_by("-id"),
        "payload": service.body_of(delivery.event).decode(),
        "error": error,
    }
    return render(request, "dashboard/delivery.html", context)


@require_safe
@owner_page
def activity(request: HttpRequest) -> HttpResponse:
    found = Action.objects.filter(user__merchant=merchant_of(request))
    recent = found.select_related("user").order_by("-id")[:100]
    return render(request, "dashboard/activity.html", {"actions": recent})

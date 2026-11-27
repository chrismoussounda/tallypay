import contextlib
import re
import uuid
from datetime import date, timedelta
from typing import Any

from django import forms
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from api.refunds import REFUSALS
from idempotency.service import KeyReused, once
from ledger import books
from ledger.models import Entry, Line
from merchants import demos
from payments import processing, service
from payments.models import Payment
from provider.models import Scenario
from refunds import processing as refund_processing
from refunds import service as refunds
from refunds.models import Refund
from webhooks.models import Delivery

from . import words
from .core import (
    DEMO_USED_UP,
    developer_page,
    done,
    merchant_of,
    merchant_page,
    page_of,
    record,
    refunds_page,
    rows,
    sheet,
    user_of,
)
from .forms import NewPayment, NewRefund

DAYS = 30
PERIODS = {"7": 7, "30": 30}
# Old enough that asking every few seconds is no longer worth it.
POLLED = timedelta(minutes=2)


@require_safe
@merchant_page
def overview(request: HttpRequest) -> HttpResponse:
    merchant = merchant_of(request)
    today = timezone.localdate()
    first = today - timedelta(days=DAYS - 1)
    recent = merchant.payments.filter(created_at__date__gte=first)
    counts = recent.aggregate(
        succeeded=Count("id", filter=Q(status="succeeded")),
        failed=Count("id", filter=Q(status="failed")),
        pending=Count("id", filter=Q(status="pending")),
        volume=Sum("amount", filter=Q(status="succeeded")),
    )
    settled = counts["succeeded"] + counts["failed"]
    context = {
        "balance": -books.balance(merchant.account),
        "volume": counts["volume"] or 0,
        "succeeded": counts["succeeded"],
        "settled": settled,
        "rate": round(100 * counts["succeeded"] / settled) if settled else None,
        "pending": counts["pending"],
        "undelivered": Delivery.objects.filter(
            endpoint__merchant=merchant, endpoint__removed_at=None, status="failed"
        ).count(),
        "chart": _chart(recent, first),
        "payments": merchant.payments.order_by("-created_at", "-id")[:5],
    }
    return render(request, "dashboard/overview.html", context)


def _chart(recent: Any, first: date) -> dict[str, Any]:
    """Bars of 30 days, drawn 720 by 160."""
    by_day = dict(
        recent.filter(status="succeeded")
        .annotate(day=TruncDate("created_at"))
        .values_list("day")
        .annotate(total=Sum("amount"))
    )
    days = [first + timedelta(days=i) for i in range(DAYS)]
    top = max(by_day.values(), default=0)
    bars = []
    for i, day in enumerate(days):
        amount = by_day.get(day, 0)
        # At least a visible sliver for a day with any money.
        height = max(round(150 * amount / top), 3) if amount else 0
        bars.append(
            {
                "day": day,
                "amount": amount,
                "x": i * 24,
                "y": 160 - height,
                "height": height,
            }
        )
    return {"bars": bars, "top": top, "first": days[0], "last": days[-1]}


@require_safe
@merchant_page
def payment_list(request: HttpRequest) -> HttpResponse:
    found = Payment.objects.filter(merchant=merchant_of(request))
    status, period = request.GET.get("status", ""), request.GET.get("period", "")
    search = request.GET.get("q", "").strip().replace("\x00", "")[:64]
    if status in Payment.Status.values:
        found = found.filter(status=status)
    if period in PERIODS:
        since = timezone.now() - timedelta(days=PERIODS[period])
        found = found.filter(created_at__gte=since)
    if search:
        matches = Q(reference__icontains=search)
        with contextlib.suppress(ValueError):
            matches |= Q(id=uuid.UUID(search))
        found = found.filter(matches)
    page, last = page_of(request, found)
    context = {
        "payments": page,
        "last": last,
        "status": status,
        "period": period,
        "q": search,
        "statuses": Payment.Status.values,
        "filtered": bool(status or period or search),
    }
    return rows(request, "dashboard/payments.html", context)


@require_safe
@merchant_page
def payment_detail(request: HttpRequest, payment_id: uuid.UUID) -> HttpResponse:
    payment = _find(request, payment_id)
    made = list(
        payment.refunds.select_related("hold", "settlement").order_by(
            "created_at", "id"
        )
    )
    refunded = refunds.refunded(payment)
    waiting = [r.created_at for r in made if r.status == "pending"]
    if payment.status == "pending":
        waiting.append(payment.created_at)
    fee = service.fee_on(payment.amount)
    context = {
        "payment": payment,
        "refunds": made,
        "refunded": refunded,
        "left": payment.amount - refunded if payment.status == "succeeded" else 0,
        "fee": fee,
        "net": payment.amount - fee,
        "timeline": _timeline(payment, made),
        "entries": _entries(payment, made),
        "hidden_fault": user_of(request).demo_id
        and payment.status != "pending"
        and payment.scenario not in Scenario.recoverable(),
        "polled": any(timezone.now() - since < POLLED for since in waiting),
    }
    # Asked for again by itself while something is waiting.
    part = "#live" if request.headers.get("HX-Target") == "live" else ""
    return render(request, f"dashboard/payment.html{part}", context)


def _entries(payment: Payment, made: list[Refund]) -> list[dict[str, Any]]:
    """What it wrote in the books, oldest first."""
    written: list[tuple[Any, Entry | None]] = [(_("Payment"), payment.entry)]
    for refund in made:
        ended = _("Failed refund, credited back")
        if refund.status == "succeeded":
            ended = _("Refund paid out")
        written.append((_("Refund, set aside"), refund.hold))
        written.append((ended, refund.settlement))
    return [
        {"what": what, **_lines(entry, payment)}
        for what, entry in written
        if entry is not None
    ]


def _lines(entry: Entry, payment: Payment) -> dict[str, Any]:
    lines = []
    for line in entry.lines.select_related("account").order_by("-amount"):
        own = line.account_id == payment.merchant.account_id
        code = line.account.code
        lines.append(
            {
                "account": payment.merchant.name if own else words.WORDS[code],
                "debit": max(line.amount, 0),
                "credit": max(-line.amount, 0),
            }
        )
    return {
        "lines": lines,
        "debit": sum(line["debit"] for line in lines),
        "credit": sum(line["credit"] for line in lines),
    }


def _timeline(payment: Payment, made: list[Refund]) -> list[dict[str, Any]]:
    """Oldest first, as it is a story."""
    steps: list[dict[str, Any]] = [
        {"at": event.created_at, "kind": event.kind, "refund": None}
        for event in payment.events.order_by("id")
    ]
    for refund in made:
        steps.append({"at": refund.created_at, "kind": "created", "refund": refund})
        if refund.settled_at:
            steps.append(
                {"at": refund.settled_at, "kind": refund.status, "refund": refund}
            )
    deliveries = (
        Delivery.objects.filter(endpoint__merchant=payment.merchant)
        .filter(
            Q(event__data__id=str(payment.id))
            | Q(event__data__payment_id=str(payment.id))
        )
        .select_related("event", "endpoint")
        .order_by("created_at", "id")
    )
    steps += [{"at": one.created_at, "delivery": one} for one in deliveries]
    return sorted(steps, key=lambda step: step["at"])


@require_http_methods(["GET", "POST"])
@developer_page
def new_payment(request: HttpRequest) -> HttpResponse:
    merchant = merchant_of(request)
    form = NewPayment(request.POST or None, initial={"once": uuid.uuid4()})
    form.for_user(user_of(request))
    # The demo's guide links here with the scenario of its first step.
    asked_for = request.GET.get("scenario", "")
    scenario = form.fields["scenario"]
    if isinstance(scenario, forms.ChoiceField) and scenario.valid_value(asked_for):
        form.initial["scenario"] = asked_for
    if request.method == "POST" and form.is_valid():
        asked = form.cleaned_data

        def action() -> dict[str, Any]:
            payment = service.create(
                merchant, asked["amount"], asked["reference"], asked["scenario"]
            )
            processing.start(payment)
            record(request, "payment_created", payment.reference)
            return {"id": str(payment.id)}

        try:
            made, _again = once(merchant, *_asked(asked, "payment"), action)
        except KeyReused:
            form.add_error(None, _("This form was already sent. Open it again."))
        except demos.UsedUp:
            form.add_error(None, str(DEMO_USED_UP))
        else:
            return done(request, _("Payment created."), "payment", made["id"])
    return _form(request, "dashboard/new_payment.html", form)


@require_http_methods(["GET", "POST"])
@refunds_page
def new_refund(request: HttpRequest, payment_id: uuid.UUID) -> HttpResponse:
    payment = _find(request, payment_id)
    merchant = payment.merchant
    left = payment.amount - refunds.refunded(payment)
    initial = {"once": uuid.uuid4(), "amount": left}
    form = NewRefund(request.POST or None, initial=initial)
    form.for_user(user_of(request))
    if request.method == "POST" and form.is_valid():
        asked = form.cleaned_data

        def action() -> dict[str, Any]:
            refund = refunds.create(
                payment.id, asked["amount"], asked["scenario"], asked["reason"]
            )
            refund_processing.start(refund)
            record(request, "refund_created", payment.reference)
            return {"id": str(refund.id)}

        try:
            once(merchant, *_asked(asked, str(payment.id)), action)
        except KeyReused:
            form.add_error(None, _("This form was already sent. Open it again."))
        except demos.UsedUp:
            form.add_error(None, str(DEMO_USED_UP))
        except tuple(REFUSALS) as refusal:
            form.add_error("amount", _(REFUSALS[type(refusal)]))
        else:
            return done(request, _("Refund started."), "payment", payment.id)
    more = {"payment": payment, "left": left}
    return _form(request, "dashboard/new_refund.html", form, more)


def _asked(asked: dict[str, Any], what: str) -> tuple[str, dict[str, Any]]:
    """Dashboard keys cannot meet those of the API, which have no spaces."""
    key = f"dashboard {asked['once']}"
    return key, {"dashboard": what} | {k: str(v) for k, v in asked.items()}


def _form(
    request: HttpRequest, template: str, form: Any, more: dict[str, Any] | None = None
) -> HttpResponse:
    status = 422 if form.errors else 200
    return sheet(request, template, {"form": form} | (more or {}), status)


def _find(request: HttpRequest, payment_id: uuid.UUID) -> Payment:
    return get_object_or_404(
        Payment.objects.select_related("merchant__account"),
        merchant=merchant_of(request),
        id=payment_id,
    )


MOVE = re.compile(r"(Payment|Refund) ([0-9a-f-]{36})( paid out| failed)?")


@require_safe
@merchant_page
def balance(request: HttpRequest) -> HttpResponse:
    merchant = merchant_of(request)
    entries = Entry.objects.filter(lines__account=merchant.account)
    page, last = page_of(request, entries)
    amounts = dict(
        Line.objects.filter(entry__in=page, account=merchant.account).values_list(
            "entry_id", "amount"
        )
    )
    moves = [_move(entry, -amounts[entry.id]) for entry in page]
    ids = [move["payment"] for move in moves if move["payment"]]
    names = dict(merchant.payments.filter(id__in=ids).values_list("id", "reference"))
    for move in moves:
        if move["payment"]:
            move["reference"] = names.get(uuid.UUID(str(move["payment"])))
    context = {
        "balance": -books.balance(merchant.account),
        "moves": moves,
        "last": last,
    }
    return rows(request, "dashboard/balance.html", context)


def _move(entry: Entry, amount: int) -> dict[str, Any]:
    found = MOVE.fullmatch(entry.description)
    payment: Any = None
    what = _("Correction")
    if found and found[1] == "Payment":
        what, payment = _("Payment, less its fee"), found[2]
    elif found:
        what = _("Failed refund, credited back") if found[3] else _("Refund")
        payment = (
            Refund.objects.filter(id=found[2])
            .values_list("payment_id", flat=True)
            .first()
        )
    return {"at": entry.created_at, "what": what, "amount": amount, "payment": payment}

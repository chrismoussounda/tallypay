import uuid
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from accounts.models import User
from ledger.models import Account, Entry
from payments.models import Payment
from reconciliation import resolving
from reconciliation.models import Discrepancy
from refunds.models import Refund

from .core import done, operator_page, page_of, record, rows, sheet, user_of
from .forms import Decision, Proposal
from .sessions import confirmed


@require_safe
@operator_page
def account_list(request: HttpRequest) -> HttpResponse:
    accounts = Account.objects.annotate(total=Sum("lines__amount", default=0))
    own = accounts.exclude(code__startswith="merchant:").order_by("kind", "code")
    owed = accounts.filter(code__startswith="merchant:")
    demo = user_of(request).demo
    if demo:
        shown = owed.filter(id=demo.merchant.account_id)
    else:
        shown = owed.filter(merchant__demo=None)
    ids = shown.values("id")
    others: Any = owed.exclude(id__in=ids).aggregate(all=Sum("total"))["all"]
    total: Any = accounts.aggregate(all=Sum("total"))["all"]
    context = {
        "accounts": [(a, _natural(a)) for a in own],
        "merchants": [(a, _natural(a)) for a in shown.order_by("total", "name")[:100]],
        "others": None if others is None else -int(others),
        "difference": int(total or 0),
        "entries": Entry.objects.count(),
    }
    return render(request, "dashboard/accounts.html", context)


def _natural(account: Any) -> int:
    """What is owed or earned is a credit, and shown as a plain amount."""
    credit = account.kind in (Account.Kind.LIABILITY, Account.Kind.INCOME)
    return int(-account.total if credit else account.total)


@require_safe
@operator_page
def discrepancy_list(request: HttpRequest) -> HttpResponse:
    found = resolving.seen_by(user_of(request))
    status = request.GET.get("status", "")
    if status in Discrepancy.Status.values:
        found = found.filter(status=status)
    page, last = page_of(request, found)
    context = {
        "discrepancies": page,
        "last": last,
        "status": status,
        "statuses": Discrepancy.Status.values,
    }
    return rows(request, "dashboard/discrepancies.html", context)


def _find(request: HttpRequest, found_id: uuid.UUID) -> Discrepancy:
    return get_object_or_404(resolving.seen_by(user_of(request)), id=found_id)


def _about(request: HttpRequest, found: Discrepancy) -> dict[str, Any]:
    payment = Payment.objects.filter(id=found.reference).first()
    refund = Refund.objects.filter(id=found.reference).first()
    paid = refund.payment if refund else payment
    names = dict(User.objects.filter(merchant=None).values_list("email", "name"))
    names["policy"] = _("TallyPay, by rule")
    return {
        "found": found,
        "original": payment or refund,
        "merchant": paid.merchant if paid else None,
        "proposed_by": names.get(found.proposed_by, found.proposed_by),
        "approved_by": names.get(found.approved_by, found.approved_by),
        "mine": found.proposed_by == user_of(request).email,
    }


@require_safe
@operator_page
def discrepancy_detail(request: HttpRequest, found_id: uuid.UUID) -> HttpResponse:
    context = _about(request, _find(request, found_id))
    return render(request, "dashboard/discrepancy.html", context)


@require_http_methods(["GET", "POST"])
@operator_page
def propose(request: HttpRequest, found_id: uuid.UUID) -> HttpResponse:
    found = _find(request, found_id)
    form = Proposal(resolving.allowed(found), request.POST or None)
    if request.method == "POST" and form.is_valid():
        asked = form.cleaned_data
        try:
            with transaction.atomic():
                resolving.propose(
                    found.id, asked["action"], user_of(request), asked["note"]
                )
                record(request, "proposal_made", found.id)
        except resolving.NotPossible as error:
            form.add_error(None, _(str(error)))
        else:
            return done(
                request,
                _("Proposed. Someone else must approve it."),
                "discrepancy",
                found.id,
            )
    context = _about(request, found) | {"form": form}
    status = 422 if form.errors else 200
    return sheet(request, "dashboard/propose.html", context, status)


@require_http_methods(["GET", "POST"])
@operator_page
def approve(request: HttpRequest, found_id: uuid.UUID) -> HttpResponse:
    found = _find(request, found_id)
    initial = {"proposal": found.proposal}
    form = Decision(request.POST or None, initial=initial)
    form.for_user(user_of(request))
    if request.method == "POST" and confirmed(request, form):
        try:
            with transaction.atomic():
                resolving.approve(
                    found.id, user_of(request), form.cleaned_data["proposal"]
                )
                record(request, "proposal_approved", found.id)
        except resolving.NotPossible as error:
            form.add_error(None, _(str(error)))
        else:
            return done(request, _("Approved."), "discrepancy", found.id)
    context = _about(request, found) | {"form": form}
    status = 422 if form.errors else 200
    return sheet(request, "dashboard/approve.html", context, status)


@require_http_methods(["GET", "POST"])
@operator_page
def reject(request: HttpRequest, found_id: uuid.UUID) -> HttpResponse:
    found = _find(request, found_id)
    context = _about(request, found)
    if request.method == "POST":
        try:
            proposal = int(request.POST.get("proposal", ""))
            with transaction.atomic():
                resolving.reject(found.id, user_of(request), proposal)
                record(request, "proposal_rejected", found.id)
        except (ValueError, resolving.NotPossible) as error:
            context["error"] = _(str(error))
            return sheet(request, "dashboard/reject.html", context, 422)
        return done(request, _("Rejected."), "discrepancy", found.id)
    return sheet(request, "dashboard/reject.html", context)

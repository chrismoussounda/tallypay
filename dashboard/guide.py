"""How far the visitor of a demo is, told by what is stored, and where to go next."""

from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from merchants.models import Demo
from provider.models import Scenario
from reconciliation import resolving
from reconciliation.models import Discrepancy, StatementLine
from webhooks.models import Delivery


@dataclass(frozen=True)
class Step:
    text: Promise
    who: str  # owner, proposer or approver
    page: str
    query: str
    button: Promise


STEPS = [
    Step(
        _(
            "As the shop, create a payment where the provider moves "
            "500 francs too much."
        ),
        "owner",
        "new-payment",
        "?scenario=wrong_amount",
        _("Create it"),
    ),
    Step(
        _(
            "Watch the signed webhook reach the shop. "
            "Switch its server off to see TallyPay try again."
        ),
        "owner",
        "webhooks",
        "",
        _("Open the webhooks"),
    ),
    Step(
        _("Become operator 1, and compare the statement with the books."),
        "proposer",
        "discrepancies",
        "",
        _("Compare as operator 1"),
    ),
    Step(
        _("Propose a solution, then become operator 2 to approve it."),
        "proposer",
        "discrepancies",
        "",
        _("Propose as operator 1"),
    ),
]

ROLES = _(
    "Operator 1 proposes a fix and operator 2 approves it: nobody can move money alone."
)


def steps(demo: Demo) -> dict[str, Any]:
    # Only the payment of step 1 leads to a discrepancy; any other one would leave
    # step 4 with nothing to propose.
    made = demo.merchant.payments.filter(
        created_at__gt=demo.created_at, scenario=Scenario.WRONG_AMOUNT
    )
    found = Discrepancy.objects.filter(
        reference__in=resolving.references_of(demo.merchant)
    )
    did = [
        made.exists(),
        Delivery.objects.filter(
            endpoint__merchant=demo.merchant, status="delivered"
        ).exists(),
        StatementLine.objects.filter(reference__in=made.values("id")).exists(),
        found.filter(status="resolved")
        .exclude(approved_by__in=["", resolving.POLICY])
        .exists(),
    ]
    listed = [
        {
            "number": number,
            "text": step.text,
            "did": one,
            "who": step.who,
            "url": reverse(step.page) + step.query,
            "button": step.button,
        }
        for number, (step, one) in enumerate(zip(STEPS, did, strict=True), start=1)
    ]
    current = next((one for one in listed if not one["did"]), None)
    # Once a fix is proposed, the last step is operator 2's.
    if (
        current is not None
        and current["number"] == len(STEPS)
        and found.filter(status=Discrepancy.Status.PROPOSED).exists()
    ):
        current = {**current, "who": "approver", "button": _("Approve as operator 2")}
    return {
        "steps": listed,
        "count": len(STEPS),
        "all": current is None,
        "current": current,
        "next": current["text"] if current else None,
        "roles": ROLES,
    }

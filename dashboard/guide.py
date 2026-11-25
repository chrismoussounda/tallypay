"""How far the visitor of a demo is, told by what is stored."""

from typing import Any

from django.utils.translation import gettext_lazy as _

from merchants.models import Demo
from reconciliation import resolving
from reconciliation.models import Discrepancy, StatementLine
from webhooks.models import Delivery

STEPS = [
    _("As the shop, create a payment where the provider moves 500 francs too much."),
    _("Watch it settle, and the signed webhook be delivered."),
    _("Become operator 1, and compare the statement with the books."),
    _("Propose a solution, then become operator 2 to approve it."),
]


def steps(demo: Demo) -> dict[str, Any]:
    made = demo.merchant.payments.filter(created_at__gt=demo.created_at)
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
    listed = [{"text": text, "did": one} for text, one in zip(STEPS, did, strict=True)]
    return {
        "steps": listed,
        "all": all(did),
        "next": next((one["text"] for one in listed if not one["did"]), None),
    }

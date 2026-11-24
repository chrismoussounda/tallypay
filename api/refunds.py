import uuid
from datetime import datetime
from typing import Annotated, Any

from django.http import HttpRequest, HttpResponse
from ninja import Header, Schema, Status
from ninja.errors import HttpError
from pydantic import ConfigDict, Field, StrictInt

from idempotency.service import once
from provider.models import Scenario
from refunds import processing, service
from refunds.models import Refund

from .core import ErrorOut, api, idempotency_key_of, merchant_of
from .payments import find_payment


class NewRefund(Schema):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[StrictInt, Field(gt=0, le=10_000_000)]
    reason: Refund.Reason = Refund.Reason.REQUESTED
    # How the simulated provider behaves with this refund.
    scenario: Scenario = Scenario.NORMAL


class RefundOut(Schema):
    id: uuid.UUID
    payment_id: uuid.UUID
    amount: int
    currency: str = "XAF"
    reason: str
    scenario: str
    status: str
    created_at: datetime


REFUSALS: dict[type[Exception], str] = {
    service.NotRefundable: "Only a payment that succeeded can be refunded.",
    service.TooMuch: "That is more than is left of the payment.",
    service.NotEnough: "That is more than your balance.",
}


@api.post(
    "/payments/{uuid:payment_id}/refunds",
    response={
        201: RefundOut,
        400: ErrorOut,
        401: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
)
def create_refund(
    request: HttpRequest,
    response: HttpResponse,
    payment_id: uuid.UUID,
    new: NewRefund,
    idempotency_key: Annotated[str, Header()] = "",
) -> Status[dict[str, Any]]:
    """Gives money back, in full or in parts. The fee is not returned."""
    key = idempotency_key_of(idempotency_key)
    payment = find_payment(request, payment_id)

    def action() -> dict[str, Any]:
        refund = service.create(payment.id, new.amount, new.scenario, new.reason)
        processing.start(refund)
        return RefundOut.from_orm(refund).model_dump(mode="json")

    asked = {"create_refund": new.model_dump(), "payment": str(payment_id)}
    try:
        body, replayed = once(merchant_of(request), key, asked, action)
    except tuple(REFUSALS) as refusal:
        raise HttpError(409, REFUSALS[type(refusal)]) from refusal
    if replayed:
        response["Idempotent-Replayed"] = "true"
    return Status(201, body)


@api.get(
    "/payments/{uuid:payment_id}/refunds",
    response={200: list[RefundOut], 401: ErrorOut, 404: ErrorOut},
)
def list_refunds(request: HttpRequest, payment_id: uuid.UUID) -> list[Refund]:
    """Newest first."""
    payment = find_payment(request, payment_id)
    return list(payment.refunds.order_by("-created_at", "-id"))


@api.get(
    "/refunds/{uuid:refund_id}", response={200: RefundOut, 401: ErrorOut, 404: ErrorOut}
)
def get_refund(request: HttpRequest, refund_id: uuid.UUID) -> Refund:
    refund = Refund.objects.filter(
        payment__merchant=merchant_of(request), id=refund_id
    ).first()
    if refund is None:
        raise HttpError(404, "No such refund.")
    return refund

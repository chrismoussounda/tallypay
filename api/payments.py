import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from ninja import Header, Query, Schema, Status
from ninja.errors import HttpError
from pydantic import ConfigDict, Field, StrictInt

from idempotency.service import KeyReused, once
from payments import processing, service
from payments.models import Payment
from provider.models import Scenario

from .core import ErrorOut, api, error, merchant_of


class NewPayment(Schema):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[StrictInt, Field(gt=0, le=10_000_000)]
    # No control characters: Postgres cannot store all of them.
    reference: Annotated[
        str, Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    ]
    # How the simulated provider behaves with this payment.
    scenario: Scenario = Scenario.NORMAL


class EventOut(Schema):
    kind: str
    created_at: datetime


class PaymentOut(Schema):
    id: uuid.UUID
    amount: int
    currency: str = "XAF"
    reference: str
    scenario: str
    status: str
    created_at: datetime


class PaymentDetail(PaymentOut):
    events: list[EventOut]


class PaymentList(Schema):
    data: list[PaymentOut]
    has_more: bool


@api.exception_handler(KeyReused)
def _key_reused(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(
        request,
        409,
        "idempotency_key_reused",
        "This Idempotency-Key was used for a different request.",
    )


@api.post(
    "/payments",
    response={
        201: PaymentOut,
        400: ErrorOut,
        401: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
    },
)
def create_payment(
    request: HttpRequest,
    response: HttpResponse,
    new: NewPayment,
    idempotency_key: Annotated[str, Header()] = "",
) -> Status[dict[str, Any]]:
    """Asks for a payment. A retry with the same Idempotency-Key never pays twice."""
    if not re.fullmatch(r"[!-~]{1,255}", idempotency_key):
        raise HttpError(400, "Send an Idempotency-Key of 1 to 255 ASCII characters.")
    merchant = merchant_of(request)

    def action() -> dict[str, Any]:
        payment = service.create(merchant, new.amount, new.reference, new.scenario)
        processing.start(payment)
        return PaymentOut.from_orm(payment).model_dump(mode="json")

    body, replayed = once(
        merchant, idempotency_key, {"create_payment": new.model_dump()}, action
    )
    if replayed:
        response["Idempotent-Replayed"] = "true"
    return Status(201, body)


@api.get(
    "/payments",
    response={200: PaymentList, 401: ErrorOut, 404: ErrorOut, 422: ErrorOut},
)
def list_payments(
    request: HttpRequest,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Newest first. Pass the last id as `after` for the next page."""
    payments = Payment.objects.filter(merchant=merchant_of(request))
    if after:
        last = _find(request, after)
        payments = payments.filter(
            Q(created_at__lt=last.created_at)
            | Q(created_at=last.created_at, id__lt=last.id)
        )
    page = list(payments.order_by("-created_at", "-id")[: limit + 1])
    return {"data": page[:limit], "has_more": len(page) > limit}


@api.get(
    "/payments/{uuid:payment_id}",
    response={200: PaymentDetail, 401: ErrorOut, 404: ErrorOut},
)
def get_payment(request: HttpRequest, payment_id: uuid.UUID) -> dict[str, Any]:
    payment = _find(request, payment_id)
    return {
        **PaymentOut.from_orm(payment).model_dump(),
        "events": list(payment.events.order_by("id")),
    }


def _find(request: HttpRequest, payment_id: uuid.UUID) -> Payment:
    found = Payment.objects.filter(merchant=merchant_of(request), id=payment_id).first()
    if found is None:
        raise HttpError(404, "No such payment.")
    return found

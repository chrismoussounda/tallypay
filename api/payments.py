import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from ninja import Header, NinjaAPI, Query, Schema, Status
from ninja.errors import AuthenticationError, HttpError, ValidationError
from ninja.security import HttpBearer
from pydantic import ConfigDict, Field, StrictInt

from idempotency.service import KeyReused, once
from merchants import keys
from merchants.models import Merchant
from payments import processing, service
from payments.models import Payment
from provider.models import Scenario


class ApiKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Merchant | None:
        return keys.owner(token)


api = NinjaAPI(title="TallyPay", version="1", auth=ApiKeyAuth(), urls_namespace="v1")


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


class ErrorOut(Schema):
    code: str
    message: str


def _merchant(request: HttpRequest) -> Merchant:
    merchant: Merchant = request.auth  # type: ignore[attr-defined]
    return merchant


def _error(request: HttpRequest, status: int, code: str, message: str) -> HttpResponse:
    return api.create_response(
        request, {"code": code, "message": message}, status=status
    )


@api.exception_handler(AuthenticationError)
def _unauthorized(request: HttpRequest, exc: Any) -> HttpResponse:
    return _error(request, 401, "unauthorized", "The API key is missing or not valid.")


@api.exception_handler(ValidationError)
def _invalid(request: HttpRequest, exc: ValidationError) -> HttpResponse:
    problems = [f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors]
    return _error(request, 422, "invalid_request", "; ".join(problems))


@api.exception_handler(KeyReused)
def _key_reused(request: HttpRequest, exc: Any) -> HttpResponse:
    return _error(
        request,
        409,
        "idempotency_key_reused",
        "This Idempotency-Key was used for a different request.",
    )


@api.exception_handler(HttpError)
def _http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    code = "not_found" if exc.status_code == 404 else "invalid_request"
    return _error(request, exc.status_code, code, str(exc))


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
    merchant = _merchant(request)

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
    payments = Payment.objects.filter(merchant=_merchant(request))
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
    found = Payment.objects.filter(merchant=_merchant(request), id=payment_id).first()
    if found is None:
        raise HttpError(404, "No such payment.")
    return found

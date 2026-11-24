import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from django.db.models import Q
from django.http import HttpRequest
from ninja import Query, Schema, Status
from ninja.errors import HttpError
from pydantic import ConfigDict, Field

from webhooks import sender, service, signing
from webhooks.models import Delivery, Endpoint

from .core import ErrorOut, api, merchant_of


class NewEndpoint(Schema):
    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(min_length=1, max_length=500)]


class EndpointOut(Schema):
    id: uuid.UUID
    url: str
    created_at: datetime


class NewEndpointOut(EndpointOut):
    # To check the signatures with.
    secret: str


class TryOut(Schema):
    status_code: int | None
    error: str
    created_at: datetime


class DeliveryOut(Schema):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    type: Annotated[str, Field(alias="event.type")]
    status: str
    tries: int
    created_at: datetime


class DeliveryDetail(DeliveryOut):
    data: dict[str, Any]
    history: list[TryOut]


class DeliveryList(Schema):
    data: list[DeliveryOut]
    has_more: bool


@api.post(
    "/webhook-endpoints",
    response={201: NewEndpointOut, 401: ErrorOut, 409: ErrorOut, 422: ErrorOut},
)
def add_endpoint(request: HttpRequest, new: NewEndpoint) -> Status[dict[str, Any]]:
    """Tells this address of every payment settled from now on."""
    try:
        endpoint = service.add_endpoint(merchant_of(request), new.url)
    except sender.Refused as refused:
        raise HttpError(422, str(refused)) from refused
    except service.TooMany as many:
        raise HttpError(409, f"At most {service.MAX_ENDPOINTS} endpoints.") from many
    return Status(
        201,
        {
            **EndpointOut.from_orm(endpoint).model_dump(),
            "secret": signing.secret_of(endpoint.id),
        },
    )


@api.get("/webhook-endpoints", response={200: list[EndpointOut], 401: ErrorOut})
def list_endpoints(request: HttpRequest) -> list[Endpoint]:
    mine = merchant_of(request).endpoints.filter(removed_at=None)
    return list(mine.order_by("created_at", "id"))


@api.delete(
    "/webhook-endpoints/{uuid:endpoint_id}",
    response={204: None, 401: ErrorOut, 404: ErrorOut},
)
def remove_endpoint(request: HttpRequest, endpoint_id: uuid.UUID) -> Status[None]:
    endpoint = (
        merchant_of(request).endpoints.filter(id=endpoint_id, removed_at=None).first()
    )
    if endpoint is None:
        raise HttpError(404, "No such endpoint.")
    service.remove_endpoint(endpoint)
    return Status(204, None)


@api.get(
    "/webhook-deliveries",
    response={200: DeliveryList, 401: ErrorOut, 404: ErrorOut, 422: ErrorOut},
)
def list_deliveries(
    request: HttpRequest,
    status: Literal["pending", "delivered", "failed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Newest first. Pass the last id as `after` for the next page."""
    found = _mine(request)
    if status:
        found = found.filter(status=status)
    if after:
        last = _find(request, after)
        found = found.filter(
            Q(created_at__lt=last.created_at)
            | Q(created_at=last.created_at, id__lt=last.id)
        )
    page = list(found.order_by("-created_at", "-id")[: limit + 1])
    return {"data": page[:limit], "has_more": len(page) > limit}


@api.get(
    "/webhook-deliveries/{uuid:delivery_id}",
    response={200: DeliveryDetail, 401: ErrorOut, 404: ErrorOut},
)
def get_delivery(request: HttpRequest, delivery_id: uuid.UUID) -> dict[str, Any]:
    delivery = _find(request, delivery_id)
    return {
        **DeliveryOut.from_orm(delivery).model_dump(by_alias=True),
        "data": delivery.event.data,
        "history": list(delivery.history.order_by("id")),
    }


@api.post(
    "/webhook-deliveries/{uuid:delivery_id}/replay",
    response={202: DeliveryOut, 401: ErrorOut, 404: ErrorOut, 409: ErrorOut},
)
def replay_delivery(request: HttpRequest, delivery_id: uuid.UUID) -> Status[Delivery]:
    """Sends the same message again, with all its retries."""
    delivery = _find(request, delivery_id)
    try:
        return Status(202, service.replay(delivery.id))
    except service.UnderWay as under_way:
        raise HttpError(409, "It is still being sent.") from under_way
    except service.Removed as removed:
        raise HttpError(409, "The endpoint was removed.") from removed


def _mine(request: HttpRequest) -> Any:
    return Delivery.objects.filter(
        endpoint__merchant=merchant_of(request)
    ).select_related("event")


def _find(request: HttpRequest, delivery_id: uuid.UUID) -> Delivery:
    found: Delivery | None = _mine(request).filter(id=delivery_id).first()
    if found is None:
        raise HttpError(404, "No such delivery.")
    return found

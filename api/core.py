import re
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.csp import CSP
from django.views.decorators.csp import csp_override
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, HttpError, ValidationError
from ninja.security import HttpBearer

from idempotency.service import KeyReused
from limits.service import Limit, TooMany
from merchants import demos, keys
from merchants.models import Merchant

# For each merchant.
LIMIT = Limit(per_second=10, burst=30)


class ApiKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Merchant | None:
        merchant = keys.owner(token)
        if merchant:
            # Not for each key, as a merchant could make more of them.
            LIMIT.take(f"api {merchant.id}")
        return merchant


# Swagger UI styles itself inline.
docs_csp: dict[str, list[str]] = {
    "default-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:"],
}
api = NinjaAPI(
    title="TallyPay",
    version="1",
    description="At most 10 requests a second, 30 at once. Above, the answer is 429 "
    "and `Retry-After` says how many seconds to wait.",
    auth=ApiKeyAuth(),
    urls_namespace="v1",
    docs_decorator=csp_override(docs_csp),
)

CODES = {404: "not_found", 409: "conflict"}


class ErrorOut(Schema):
    code: str
    message: str


def merchant_of(request: HttpRequest) -> Merchant:
    merchant: Merchant = request.auth  # type: ignore[attr-defined]
    return merchant


def idempotency_key_of(value: str) -> str:
    if not re.fullmatch(r"[!-~]{1,255}", value):
        raise HttpError(400, "Send an Idempotency-Key of 1 to 255 ASCII characters.")
    return value


def error(request: HttpRequest, status: int, code: str, message: str) -> HttpResponse:
    return api.create_response(
        request, {"code": code, "message": message}, status=status
    )


@api.exception_handler(AuthenticationError)
def _unauthorized(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(request, 401, "unauthorized", "The API key is missing or not valid.")


@api.exception_handler(TooMany)
def _too_many(request: HttpRequest, exc: TooMany) -> HttpResponse:
    response = error(
        request, 429, "too_many_requests", "Too many requests. " + str(exc)
    )
    response["Retry-After"] = str(exc.wait)
    return response


@api.exception_handler(demos.UsedUp)
def _used_up(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(
        request, 403, "demo_used_up", "The demo has reached its limit for today."
    )


@api.exception_handler(demos.NotInDemo)
def _not_in_demo(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(request, 422, "not_in_demo", "A demo does not have this scenario.")


@api.exception_handler(ValidationError)
def _invalid(request: HttpRequest, exc: ValidationError) -> HttpResponse:
    problems = [f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors]
    return error(request, 422, "invalid_request", "; ".join(problems))


@api.exception_handler(KeyReused)
def _key_reused(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(
        request,
        409,
        "idempotency_key_reused",
        "This Idempotency-Key was used for a different request.",
    )


@api.exception_handler(HttpError)
def _http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    code = CODES.get(exc.status_code, "invalid_request")
    return error(request, exc.status_code, code, str(exc))

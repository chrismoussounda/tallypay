from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, HttpError, ValidationError
from ninja.security import HttpBearer

from merchants import keys
from merchants.models import Merchant


class ApiKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Merchant | None:
        return keys.owner(token)


api = NinjaAPI(title="TallyPay", version="1", auth=ApiKeyAuth(), urls_namespace="v1")

CODES = {404: "not_found", 409: "conflict"}


class ErrorOut(Schema):
    code: str
    message: str


def merchant_of(request: HttpRequest) -> Merchant:
    merchant: Merchant = request.auth  # type: ignore[attr-defined]
    return merchant


def error(request: HttpRequest, status: int, code: str, message: str) -> HttpResponse:
    return api.create_response(
        request, {"code": code, "message": message}, status=status
    )


@api.exception_handler(AuthenticationError)
def _unauthorized(request: HttpRequest, exc: Any) -> HttpResponse:
    return error(request, 401, "unauthorized", "The API key is missing or not valid.")


@api.exception_handler(ValidationError)
def _invalid(request: HttpRequest, exc: ValidationError) -> HttpResponse:
    problems = [f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors]
    return error(request, 422, "invalid_request", "; ".join(problems))


@api.exception_handler(HttpError)
def _http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    code = CODES.get(exc.status_code, "invalid_request")
    return error(request, exc.status_code, code, str(exc))

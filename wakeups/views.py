from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import qstash
from .models import WakeUp


def _last() -> dict[str, WakeUp | None]:
    return {"wakeup": WakeUp.objects.order_by("-received_at").first()}


@require_GET
def last_wakeup(request: HttpRequest) -> HttpResponse:
    return render(request, "last_wakeup.html", _last())


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def wake(request: HttpRequest) -> HttpResponse:
    call = qstash.read_signed(request)
    if call is None:
        return HttpResponse(status=401)
    wakeup_id, due_at = call
    # QStash may deliver the same call twice.
    WakeUp.objects.get_or_create(id=wakeup_id, defaults={"due_at": due_at})
    return HttpResponse(status=204)

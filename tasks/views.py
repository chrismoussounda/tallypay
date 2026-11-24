import hmac
import uuid

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from config import qstash
from reconciliation import matching

from . import queue


@csrf_exempt
@require_POST
def wake(request: HttpRequest) -> HttpResponse:
    data = qstash.read_signed(request)
    try:
        task_id = uuid.UUID(data["task"]) if data else None
    except (KeyError, ValueError, AttributeError, TypeError):
        task_id = None
    if task_id is None:
        return HttpResponse(status=401)
    # QStash calls again after anything but a success.
    return HttpResponse(status=204 if queue.run(task_id) else 409)


@require_GET
def cron(request: HttpRequest) -> HttpResponse:
    """Called by Vercel every day: the tasks QStash missed, then reconciliation."""
    expected = f"Bearer {settings.CRON_SECRET}"
    given = request.headers.get("Authorization", "")
    if not settings.CRON_SECRET or not hmac.compare_digest(
        given.encode(), expected.encode()
    ):
        return HttpResponse(status=401)
    done = queue.run_due(seconds=120)
    return JsonResponse({"done": done, "discrepancies": matching.run()})

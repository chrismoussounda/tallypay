import uuid

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from config import qstash

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

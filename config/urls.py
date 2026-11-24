from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView

from api import urls as api
from tasks import views as task_views
from wakeups import views

urlpatterns = [
    path("", views.home, name="home"),
    path("v1/", api.urls),
    # Asked for by pages without a link to it, such as the API documentation.
    path("favicon.ico", RedirectView.as_view(url=static("favicon.ico"))),
    path("healthz", views.health, name="health"),
    path("last-wakeup", views.last_wakeup, name="last-wakeup"),
    path("internal/tasks", task_views.wake, name="tasks"),
    path("internal/wake", views.wake, name="wake"),
]

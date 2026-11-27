from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from api import urls as api
from config import looks
from demos import views as demo_views
from tasks import views as task_views
from wakeups import views

urlpatterns = [
    path("", demo_views.home, name="home"),
    path("about", demo_views.about, name="about"),
    path("app/", include("dashboard.urls")),
    path("demo/open", demo_views.open_demo, name="open-demo"),
    path("demo/inbox/<uuid:merchant_id>", demo_views.inbox, name="demo-inbox"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("theme", looks.set_theme, name="set-theme"),
    path("v1/", api.urls),
    # Asked for by pages without a link to it, such as the API documentation.
    path("favicon.ico", RedirectView.as_view(url=static("favicon.ico"))),
    path("healthz", views.health, name="health"),
    path("last-wakeup", views.last_wakeup, name="last-wakeup"),
    path("internal/cron", task_views.cron, name="cron"),
    path("internal/tasks", task_views.wake, name="tasks"),
    path("internal/wake", views.wake, name="wake"),
]

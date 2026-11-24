from django.urls import path

from api.payments import api
from wakeups import views

urlpatterns = [
    path("", views.home, name="home"),
    path("v1/", api.urls),
    path("healthz", views.health, name="health"),
    path("last-wakeup", views.last_wakeup, name="last-wakeup"),
    path("internal/wake", views.wake, name="wake"),
]

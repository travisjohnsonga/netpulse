from django.urls import path

from .views import metrics_stub

urlpatterns = [
    path("metrics/", metrics_stub, name="telemetry-metrics"),
]

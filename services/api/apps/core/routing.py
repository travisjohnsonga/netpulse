from django.urls import re_path

from apps.alerts.consumers import AlertConsumer
from apps.telemetry.consumers import TelemetryConsumer

websocket_urlpatterns = [
    re_path(r"^ws/telemetry/$", TelemetryConsumer.as_asgi()),
    re_path(r"^ws/alerts/$", AlertConsumer.as_asgi()),
]

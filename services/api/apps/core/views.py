import os
import socket
import urllib.request
import urllib.error

from django.db import connection
from django.db.utils import OperationalError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except OperationalError:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return Response({"status": status, "db": db_ok}, status=200 if db_ok else 503)


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


@api_view(["GET"])
@permission_classes([AllowAny])
def infrastructure_health(request):
    valkey_host = os.environ.get("VALKEY_HOST", "valkey")
    valkey_port = int(os.environ.get("VALKEY_PORT", "6379"))
    nats_host = os.environ.get("NATS_HOST", "nats")
    influxdb_url = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
    opensearch_host = os.environ.get("OPENSEARCH_HOST", "opensearch")
    opensearch_port = int(os.environ.get("OPENSEARCH_PORT", "9200"))

    try:
        connection.ensure_connection()
        postgres_ok = True
    except OperationalError:
        postgres_ok = False

    services = {
        "postgres": postgres_ok,
        "valkey": _tcp_ok(valkey_host, valkey_port),
        "nats": _tcp_ok(nats_host, 4222),
        "influxdb": _http_ok(f"{influxdb_url}/health"),
        "opensearch": _http_ok(
            f"http://{opensearch_host}:{opensearch_port}/_cluster/health"
        ),
    }

    return Response({"services": services})

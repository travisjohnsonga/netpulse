"""
aiohttp-based OTLP HTTP receiver on port 4318.

Endpoints:
  POST /v1/metrics   — Content-Type: application/x-protobuf or application/json
  POST /v1/logs
  POST /v1/traces
  POST /v1/metrics/json  — convenience alias (always JSON)

Returns {"partialSuccess": {}} JSON on success (HTTP 200).
Max request body: 4 MB.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from .normalizer import (
    parse_logs,
    parse_logs_json,
    parse_metrics,
    parse_metrics_json,
    parse_traces,
    parse_traces_json,
)

if TYPE_CHECKING:
    from .publisher import NATSPublisher

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 4 * 1024 * 1024  # 4 MB
_SUCCESS_BODY = b'{"partialSuccess":{}}'


def _exporter_ip(request: web.Request) -> str:
    """Return the sender's IP address."""
    peername = request.transport.get_extra_info("peername") if request.transport else None
    if peername:
        return peername[0]
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return "unknown"


def _is_json(request: web.Request) -> bool:
    ct = request.content_type or ""
    return "json" in ct


async def _read_body(request: web.Request) -> bytes:
    """Read request body, enforcing the 4 MB size limit."""
    content_length = request.content_length
    if content_length is not None and content_length > _MAX_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=_MAX_BODY_BYTES, actual_size=content_length)
    data = await request.read()
    if len(data) > _MAX_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=_MAX_BODY_BYTES, actual_size=len(data))
    return data


def _ok_response() -> web.Response:
    return web.Response(
        status=200,
        content_type="application/json",
        body=_SUCCESS_BODY,
    )


def build_app(publisher: "NATSPublisher") -> web.Application:
    app = web.Application()

    # ------------------------------------------------------------------
    # /v1/metrics
    # ------------------------------------------------------------------
    async def handle_metrics(request: web.Request) -> web.Response:
        data = await _read_body(request)
        ip = _exporter_ip(request)
        try:
            if _is_json(request):
                items = parse_metrics_json(data, ip)
            else:
                try:
                    items = parse_metrics(data, ip)
                except ImportError:
                    logger.warning("opentelemetry-proto unavailable; trying JSON fallback for metrics")
                    items = parse_metrics_json(data, ip)

            for item in items:
                await publisher.publish_metrics(ip, item.to_dict())

            logger.debug("metrics from %s: %d metric(s)", ip, len(items))
        except Exception as exc:
            logger.error("error processing metrics from %s: %s", ip, exc)
            raise web.HTTPBadRequest(reason=str(exc)) from exc
        return _ok_response()

    # ------------------------------------------------------------------
    # /v1/logs
    # ------------------------------------------------------------------
    async def handle_logs(request: web.Request) -> web.Response:
        data = await _read_body(request)
        ip = _exporter_ip(request)
        try:
            if _is_json(request):
                items = parse_logs_json(data, ip)
            else:
                try:
                    items = parse_logs(data, ip)
                except ImportError:
                    logger.warning("opentelemetry-proto unavailable; trying JSON fallback for logs")
                    items = parse_logs_json(data, ip)

            for item in items:
                await publisher.publish_logs(ip, item.to_dict())

            logger.debug("logs from %s: %d record(s)", ip, len(items))
        except Exception as exc:
            logger.error("error processing logs from %s: %s", ip, exc)
            raise web.HTTPBadRequest(reason=str(exc)) from exc
        return _ok_response()

    # ------------------------------------------------------------------
    # /v1/traces
    # ------------------------------------------------------------------
    async def handle_traces(request: web.Request) -> web.Response:
        data = await _read_body(request)
        ip = _exporter_ip(request)
        try:
            if _is_json(request):
                items = parse_traces_json(data, ip)
            else:
                try:
                    items = parse_traces(data, ip)
                except ImportError:
                    logger.warning("opentelemetry-proto unavailable; trying JSON fallback for traces")
                    items = parse_traces_json(data, ip)

            for item in items:
                await publisher.publish_traces(ip, item.to_dict())

            logger.debug("traces from %s: %d span(s)", ip, len(items))
        except Exception as exc:
            logger.error("error processing traces from %s: %s", ip, exc)
            raise web.HTTPBadRequest(reason=str(exc)) from exc
        return _ok_response()

    # ------------------------------------------------------------------
    # /v1/metrics/json — convenience JSON-only alias
    # ------------------------------------------------------------------
    async def handle_metrics_json(request: web.Request) -> web.Response:
        data = await _read_body(request)
        ip = _exporter_ip(request)
        try:
            items = parse_metrics_json(data, ip)
            for item in items:
                await publisher.publish_metrics(ip, item.to_dict())
            logger.debug("metrics/json from %s: %d metric(s)", ip, len(items))
        except Exception as exc:
            logger.error("error processing metrics/json from %s: %s", ip, exc)
            raise web.HTTPBadRequest(reason=str(exc)) from exc
        return _ok_response()

    app.router.add_post("/v1/metrics", handle_metrics)
    app.router.add_post("/v1/logs", handle_logs)
    app.router.add_post("/v1/traces", handle_traces)
    app.router.add_post("/v1/metrics/json", handle_metrics_json)

    return app

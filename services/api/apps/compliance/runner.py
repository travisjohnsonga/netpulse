"""On-demand compliance runs (single device + fleet-wide).

A fleet run re-evaluates template compliance for every active device and
persists the **weighted** DeviceComplianceScore (template + interface + role +
startup) so the device list and the Compliance tab agree. Role-consistency
checks can reach live devices over REST, so a full run can take a while — it is
therefore executed in a **background thread** with progress published to the
shared (Valkey) cache, which every gunicorn worker can read. The HTTP request
returns immediately and the UI polls ``status``.

Security: a per-device failure is logged server-side with full detail; only a
**generic** message ever reaches the API response (CodeQL information-exposure).
"""
from __future__ import annotations

import logging
import threading

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

_LOCK_KEY = "compliance_run_all_lock"
_STATUS_KEY = "compliance_run_all_status"
_TTL = 7200          # 2h safety expiry so a crashed run can't wedge the lock
_MAX_ERRORS = 50     # cap the error list returned to the UI


def _idle_status() -> dict:
    return {"running": False, "total": 0, "done": 0, "success": 0,
            "failed": 0, "errors": [], "started_at": None, "finished_at": None}


def get_status() -> dict:
    return cache.get(_STATUS_KEY) or _idle_status()


def _set_status(status: dict) -> None:
    cache.set(_STATUS_KEY, status, timeout=_TTL)


def run_one(device, role_cache=None) -> dict:
    """Re-run template compliance for one device and persist its weighted score.

    Returns the score dict from ``run_and_store_compliance``. Raises on failure
    (callers scrub the exception).
    """
    from .device_score import run_and_store_compliance
    from .engine import run_compliance_for_device
    # store_score=False here — we store once below with the shared role_cache
    # (so a fleet run evaluates each role rule once) and need the score dict back.
    run_compliance_for_device(device, store_score=False)   # refresh template results
    return run_and_store_compliance(device, role_cache=role_cache)


def _run_worker(device_ids: list[int] | None) -> None:
    from django.db import close_old_connections

    from apps.devices.models import Device
    close_old_connections()
    try:
        qs = Device.objects.filter(status=Device.Status.ACTIVE)
        if device_ids:
            qs = qs.filter(id__in=device_ids)
        devices = list(qs.only("id", "hostname"))

        status = _idle_status()
        status.update({"running": True, "total": len(devices),
                       "started_at": timezone.now().isoformat()})
        _set_status(status)

        role_cache: dict = {}
        for device in devices:
            try:
                run_one(device, role_cache=role_cache)
                status["success"] += 1
            except Exception:  # noqa: BLE001 — one device must not abort the run
                logger.error("compliance run failed for %s", device.hostname, exc_info=True)
                status["failed"] += 1
                if len(status["errors"]) < _MAX_ERRORS:
                    # Generic message only — never the exception text.
                    status["errors"].append({"device": device.hostname,
                                             "error": "compliance run failed"})
            status["done"] += 1
            _set_status(status)
    except Exception:  # noqa: BLE001
        logger.error("compliance run-all worker crashed", exc_info=True)
    finally:
        status = get_status()
        status["running"] = False
        status["finished_at"] = timezone.now().isoformat()
        _set_status(status)
        cache.delete(_LOCK_KEY)
        close_old_connections()


def start_run_all(device_ids: list[int] | None = None) -> tuple[bool, dict]:
    """Start a background fleet compliance run.

    Returns ``(started, status)``. ``started`` is False (with the live status)
    when a run is already in progress — callers should return 409.
    """
    if not cache.add(_LOCK_KEY, True, timeout=_TTL):
        return False, get_status()

    # Count up front so the very first status poll already has a denominator.
    from apps.devices.models import Device
    qs = Device.objects.filter(status=Device.Status.ACTIVE)
    if device_ids:
        qs = qs.filter(id__in=device_ids)
    total = qs.count()

    status = _idle_status()
    status.update({"running": True, "total": total,
                   "started_at": timezone.now().isoformat()})
    _set_status(status)

    threading.Thread(target=_run_worker, args=(device_ids,),
                     name="compliance-run-all", daemon=True).start()
    return True, status


def run_all_blocking(device_ids: list[int] | None = None) -> bool:
    """Run a fleet compliance pass **synchronously** (for the scheduler process).

    Returns False without running if a run is already in progress (the HTTP
    background run or another scheduler tick holds the lock). The worker releases
    the lock and finalises the status on exit.
    """
    if not cache.add(_LOCK_KEY, True, timeout=_TTL):
        logger.info("compliance run-all skipped — a run is already in progress")
        return False
    _run_worker(device_ids)   # sets status, processes inline, frees the lock
    return True

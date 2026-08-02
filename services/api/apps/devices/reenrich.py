"""Fleet-wide identity re-enrichment (daily + on-demand).

Re-probes every active device's **identity** fields — model / os_version /
serial_number / vendor / platform — so they don't silently go stale after a
firmware upgrade or hardware swap. It runs the lightweight
:func:`apps.devices.enrich.refresh_device_identity` (REST/SNMP/SSH only), NOT
the full enrich pipeline: interface discovery, LLDP and config collection are
heavier and already have their own triggers/cadence.

Design (mirrors :mod:`apps.compliance.runner`):

* A single background thread processes devices **sequentially** — so it never
  opens many SNMP/SSH/REST sessions at once — with an **adaptive per-device
  stagger** that spreads the batch across a target window (protects both
  spane's own workers and the customer's network from a thundering herd).
* A Valkey lock + status cache make it safe to trigger from the API (returns
  immediately, UI polls ``status``) and from the scheduler; the two can never
  overlap.
* A per-device ``try/except`` means one unreachable device never aborts the
  batch; failures are counted and summarised.

The scheduler starts it in the background (never blocking the scheduler loop,
which would stall heartbeats/liveness for the whole stagger window).

Security: a per-device failure is logged server-side with full detail; only a
**generic** message ever reaches the cached status / API response (CodeQL
information-exposure).
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

_LOCK_KEY = "device_reenrich_lock"
_STATUS_KEY = "device_reenrich_status"
_MAX_ERRORS = 50     # cap the error list kept in the status
_MAX_CHANGES = 100   # cap the per-device change list kept in the status

# ── Config (all overridable via env) ───────────────────────────────────────────
# Master on/off — some environments don't want daily SNMP/SSH/REST hits to every
# device (e.g. security-sensitive networks). Disables ONLY the scheduled run;
# an admin can still trigger a manual run explicitly.
def _enabled() -> bool:
    return os.environ.get("REENRICH_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

# Hour-of-day (0–23, server tz) for the daily run. Default 04:00 — after the
# 03:00 compliance pass, which is after the 02:00 config backup.
def _run_hour() -> int:
    try:
        return max(0, min(23, int(os.environ.get("REENRICH_RUN_HOUR", "4"))))
    except ValueError:
        return 4

# Target wall-clock window (seconds) to spread the whole batch across. The
# per-device delay is derived from this and the fleet size, so a big fleet
# self-throttles into the window instead of using a fixed (and possibly huge)
# total time. Default 1h.
def _stagger_window_s() -> int:
    try:
        return max(0, int(os.environ.get("REENRICH_STAGGER_WINDOW_S", str(3600))))
    except ValueError:
        return 3600

# Hard cap on the delay between two devices, so a tiny fleet doesn't sleep for
# window/2 between two boxes. Default 30s.
def _max_stagger_s() -> float:
    try:
        return max(0.0, float(os.environ.get("REENRICH_MAX_STAGGER_S", "30")))
    except ValueError:
        return 30.0

_LAST_RUN_KEY = "device_reenrich_last_scheduled_run"   # SystemSetting → ISO date


def _idle_status() -> dict:
    return {"running": False, "total": 0, "done": 0, "success": 0, "failed": 0,
            "unreachable": 0, "updated": 0, "errors": [], "changes": [],
            "started_at": None, "finished_at": None, "trigger": None}


def get_status() -> dict:
    return cache.get(_STATUS_KEY) or _idle_status()


def _set_status(status: dict, ttl: int) -> None:
    cache.set(_STATUS_KEY, status, timeout=ttl)


def _per_device_delay(n: int) -> float:
    """Base per-device stagger for a batch of ``n`` devices (before jitter)."""
    if n <= 1:
        return 0.0
    window = _stagger_window_s()
    if window <= 0:
        return 0.0
    # Spread across the window, but never longer than the hard cap.
    return min(window / n, _max_stagger_s())


def _run_worker(device_ids: list[int] | None, trigger: str, ttl: int) -> None:
    from django.db import close_old_connections

    from .enrich import refresh_device_identity
    from .models import Device

    close_old_connections()
    status = _idle_status()
    try:
        qs = Device.objects.filter(status=Device.Status.ACTIVE,
                                   credential_profile__isnull=False)
        if device_ids:
            qs = qs.filter(id__in=device_ids)
        devices = list(qs.only("id", "hostname"))

        n = len(devices)
        base_delay = _per_device_delay(n)
        status.update({"running": True, "total": n, "trigger": trigger,
                       "started_at": timezone.now().isoformat()})
        _set_status(status, ttl)
        logger.info("fleet re-enrichment starting: %d device(s), trigger=%s, "
                    "~%.1fs stagger/device (window=%ds, cap=%.0fs)",
                    n, trigger, base_delay, _stagger_window_s(), _max_stagger_s())

        for i, device in enumerate(devices):
            try:
                detail: dict = {}
                refresh_device_identity(device.id, result=detail)
                changed = detail.get("changed") or {}
                errors = detail.get("errors") or []
                if errors:
                    # A recorded step error (e.g. couldn't read credentials).
                    raise RuntimeError(errors[0].get("message", "identity refresh failed"))
                if detail.get("reachable") is False:
                    # Collectors swallow connection failures + return {}; treat a
                    # device that returned no data as unreachable, not "success".
                    status["failed"] += 1
                    status["unreachable"] += 1
                    if len(status["errors"]) < _MAX_ERRORS:
                        status["errors"].append({"device": device.hostname,
                                                 "error": "unreachable — no response"})
                else:
                    status["success"] += 1
                    if changed:
                        status["updated"] += 1
                        if len(status["changes"]) < _MAX_CHANGES:
                            status["changes"].append({"device": device.hostname,
                                                      "fields": sorted(changed.keys())})
                        logger.info("re-enrich: %s updated %s", device.hostname,
                                    {k: changed[k] for k in changed})
            except Exception as exc:  # noqa: BLE001 — one device must not abort the batch
                logger.warning("re-enrich failed for %s (id=%s): %s",
                               device.hostname, device.id, exc)
                status["failed"] += 1
                if len(status["errors"]) < _MAX_ERRORS:
                    # Generic label only — never the exception text (CodeQL).
                    status["errors"].append({"device": device.hostname,
                                             "error": "identity refresh failed"})
            status["done"] += 1
            _set_status(status, ttl)

            # Stagger: sleep between devices (not after the last), with ±50%
            # jitter so successive runs/devices don't align to the same instants.
            if base_delay and i < n - 1:
                time.sleep(base_delay * random.uniform(0.5, 1.0))
    except Exception:  # noqa: BLE001
        logger.error("fleet re-enrichment worker crashed", exc_info=True)
    finally:
        status = get_status()
        status["running"] = False
        status["finished_at"] = timezone.now().isoformat()
        _set_status(status, ttl)
        cache.delete(_LOCK_KEY)
        close_old_connections()
        failed = status.get("failed", 0)
        summary = (f"fleet re-enrichment complete: {status.get('success', 0)}/"
                   f"{status.get('total', 0)} refreshed "
                   f"({status.get('updated', 0)} changed), {failed} failed "
                   f"({status.get('unreachable', 0)} unreachable)")
        if failed:
            hosts = ", ".join(e["device"] for e in status.get("errors", [])[:20])
            logger.warning("%s: [%s]", summary, hosts)
        else:
            logger.info("%s", summary)


def _ttl_for_run() -> int:
    """Lock/status TTL — long enough to outlive the whole staggered window."""
    return max(7200, _stagger_window_s() + 3600)


def start_reenrich_all(device_ids: list[int] | None = None,
                       trigger: str = "manual") -> tuple[bool, dict]:
    """Start a background fleet identity re-enrichment.

    Returns ``(started, status)``. ``started`` is False (with the live status)
    when a run is already in progress — API callers should return 409.
    """
    ttl = _ttl_for_run()
    if not cache.add(_LOCK_KEY, True, timeout=ttl):
        return False, get_status()

    from .models import Device
    qs = Device.objects.filter(status=Device.Status.ACTIVE,
                               credential_profile__isnull=False)
    if device_ids:
        qs = qs.filter(id__in=device_ids)
    total = qs.count()

    status = _idle_status()
    status.update({"running": True, "total": total, "trigger": trigger,
                   "started_at": timezone.now().isoformat()})
    _set_status(status, ttl)

    threading.Thread(target=_run_worker, args=(device_ids, trigger, ttl),
                     name="device-reenrich-all", daemon=True).start()
    return True, status


# ── Scheduler hook (hour-gated + same-day deduped, mirrors compliance) ──────────
def _is_due(now) -> bool:
    if not _enabled():
        return False
    if now.hour != _run_hour():
        return False
    from apps.core.models import SystemSetting
    return SystemSetting.get(_LAST_RUN_KEY) != now.date().isoformat()


def run_due_reenrichment(now=None) -> bool:
    """Kick off the daily fleet re-enrichment if today's run is due.

    Non-blocking: starts a background thread and returns immediately so the
    scheduler loop keeps ticking through the (possibly multi-hour) stagger
    window. Returns True if a run was started this tick.
    """
    now = now or timezone.now()
    if not _is_due(now):
        return False

    from apps.core.models import SystemSetting
    # Mark the day done up-front so a long run (or a second tick within the
    # hour) can't re-trigger it; a failure won't retry until tomorrow.
    SystemSetting.set(_LAST_RUN_KEY, now.date().isoformat())
    logger.info("scheduler: starting daily fleet re-enrichment (hour=%02d:00)", _run_hour())
    started, _ = start_reenrich_all(trigger="scheduled")
    if not started:
        logger.info("scheduler: daily re-enrichment skipped — another run is active")
    return started

"""
Per-device SNMP poller.

Each device gets a dedicated asyncio task per PollProfile that runs an
infinite loop: GET the configured OIDs, publish results, sleep for the
configured interval.  If credentials are unavailable or the device times
out, the error is logged and the task sleeps before retrying.

Credentials are fetched from OpenBao via CredentialManager (cached, never
stored in memory beyond TTL).  SNMPv1/v2c use CommunityData; v3 uses
UsmUserData built from the vault secret.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .credentials import CredentialError, CredentialManager
from .mib_resolver import resolve
from .models import Device, PollProfile, build_community_data, build_usm_data
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)


class SNMPPoller:
    def __init__(
        self,
        credentials: CredentialManager,
        publisher: NATSPublisher,
        poll_timeout: float = 5.0,
        poll_retries: int = 1,
    ) -> None:
        self._creds = credentials
        self._pub = publisher
        self._timeout = poll_timeout
        self._retries = poll_retries
        self._tasks: dict[str, list[asyncio.Task]] = {}
        # One shared SNMP engine for all polls (pysnmp is not thread-safe but is coro-safe)
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from pysnmp.hlapi.asyncio import SnmpEngine
            self._engine = SnmpEngine()
        return self._engine

    # ── Device lifecycle ──────────────────────────────────────────────────────

    def upsert(self, device: Device) -> None:
        """Add or replace a device's polling tasks."""
        self._stop_device(device.device_id)
        if not device.poll_profiles:
            logger.info("device %s has no poll profiles — not polling", device.device_id)
            return
        tasks = [
            asyncio.create_task(
                self._poll_loop(device, profile),
                name=f"poll-{device.device_id}-{profile.name}",
            )
            for profile in device.poll_profiles
        ]
        self._tasks[device.device_id] = tasks
        logger.info(
            "polling %s (%s) with %d profile(s)",
            device.device_id, device.ip, len(tasks),
        )

    def remove(self, device_id: str) -> None:
        self._stop_device(device_id)
        logger.info("removed device %s from poller", device_id)

    def stop_all(self) -> None:
        for device_id in list(self._tasks):
            self._stop_device(device_id)

    def _stop_device(self, device_id: str) -> None:
        for task in self._tasks.pop(device_id, []):
            task.cancel()

    # ── Poll loop ─────────────────────────────────────────────────────────────

    async def _poll_loop(self, device: Device, profile: PollProfile) -> None:
        logger.debug("poll loop started: %s / %s", device.device_id, profile.name)
        while True:
            try:
                await self._poll(device, profile)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("poll error for %s/%s: %s", device.device_id, profile.name, exc)
            await asyncio.sleep(profile.interval_seconds)

    async def _poll(self, device: Device, profile: PollProfile) -> None:
        if not profile.oids:
            return

        try:
            creds = await self._creds.get(device.cred_path) if device.cred_path else {}
        except CredentialError as exc:
            logger.warning("credential error for %s: %s — skipping poll", device.device_id, exc)
            return

        t0 = time.monotonic()
        try:
            metrics = await self._snmp_get(device, profile.oids, creds)
        except Exception as exc:
            logger.warning("SNMP GET failed for %s (%s): %s", device.device_id, device.ip, exc)
            return

        duration_ms = round((time.monotonic() - t0) * 1000)

        payload = {
            "device_id": device.device_id,
            "hostname": device.hostname,
            "ip": device.ip,
            "profile": profile.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "poll_duration_ms": duration_ms,
            "metrics": metrics,
        }
        await self._pub.publish_metrics(device.device_id, payload)
        logger.debug(
            "polled %s/%s: %d OIDs in %dms",
            device.device_id, profile.name, len(metrics), duration_ms,
        )

    # ── SNMP execution ────────────────────────────────────────────────────────

    async def _snmp_get(
        self,
        device: Device,
        oids: list[str],
        creds: dict[str, Any],
    ) -> dict[str, Any]:
        from pysnmp.hlapi.asyncio import (
            ContextData,
            ObjectIdentity,
            ObjectType,
            UdpTransportTarget,
            getCmd,
        )

        engine = self._get_engine()
        auth = (
            build_usm_data(creds) if device.version == 3
            else build_community_data(device.version, creds)
        )
        target = UdpTransportTarget(
            (device.ip, device.port),
            timeout=self._timeout,
            retries=self._retries,
        )
        oid_objects = [ObjectType(ObjectIdentity(oid)) for oid in oids]

        error_indication, error_status, error_index, var_binds = await getCmd(
            engine, auth, target, ContextData(), *oid_objects
        )

        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            idx = int(error_status) - 1
            bad = oids[idx] if 0 <= idx < len(oids) else "?"
            raise RuntimeError(f"errorStatus {error_status} at OID {bad}")

        metrics: dict[str, Any] = {}
        for vb in var_binds:
            oid_str = str(vb[0])
            val_obj = vb[1]
            try:
                val_str = val_obj.prettyPrint()
                type_name = type(val_obj).__name__
            except Exception:
                val_str = repr(val_obj)
                type_name = "Unknown"
            mib, name, instance = resolve(oid_str)
            metrics[oid_str] = {
                "oid": oid_str,
                "name": f"{name}.{instance}" if instance else name,
                "mib": mib,
                "value": val_str,
                "type": type_name,
            }
        return metrics

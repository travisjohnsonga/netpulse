"""
Entry point: python -m ingest.snmp_server

Starts two concurrent subsystems:
  1. SNMPTrapReceiver   — UDP/162 trap + SNMPv3 inform listener
  2. SNMPPoller         — scheduled per-device SNMPv1/v2c/v3 GET cycles

Device configuration arrives via:
  a. DEVICES_JSON env var   — parsed once at startup
  b. NATS netpulse.devices.upsert / netpulse.devices.remove — live updates
"""
import asyncio
import json
import logging
import signal

from .config import cfg, _resolve_openbao_token
from .credentials import CredentialManager
from .gnmi_state import GNMIActivity
from .models import Device
from .poller import SNMPPoller
from .publisher import NATSPublisher
from .trap_receiver import SNMPTrapReceiver

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    # ── MIB sources (mounted /app/mibs) for OID resolution ────────────────────
    from .mib_sources import register_mib_sources
    register_mib_sources()

    # ── NATS publisher ────────────────────────────────────────────────────────
    publisher = NATSPublisher(
        url=cfg.nats_url,
        user=cfg.nats_user,
        password=cfg.nats_password,
        prefix=cfg.metrics_prefix,
        stream_name=cfg.stream_name,
        stream_max_age_seconds=cfg.stream_max_age_seconds,
    )
    await publisher.connect()

    # ── OpenBao credential manager ────────────────────────────────────────────
    # Pass the resolver (not cfg.openbao_token, which is frozen at import time):
    # on a reboot race the token may not be readable yet when this module is
    # imported, so the manager re-reads it on demand and self-heals once
    # .init_keys is present and OpenBao is unsealed.
    creds = CredentialManager(
        addr=cfg.openbao_addr,
        token_provider=_resolve_openbao_token,
        cache_ttl=cfg.cred_cache_ttl,
    )

    # ── Adaptive polling (gNMI heartbeat in Valkey) ───────────────────────────
    gnmi_activity = (
        GNMIActivity(url=cfg.valkey_url, threshold_seconds=cfg.gnmi_active_threshold)
        if cfg.adaptive_polling else None
    )
    if gnmi_activity is None:
        logger.info("adaptive polling disabled — SNMP polls all devices")

    # ── Poller ────────────────────────────────────────────────────────────────
    poller = SNMPPoller(
        credentials=creds,
        publisher=publisher,
        poll_timeout=cfg.poll_timeout,
        poll_retries=cfg.poll_retries,
        gnmi_activity=gnmi_activity,
    )

    for raw in cfg.load_devices():
        try:
            device = Device.from_dict(raw)
            poller.upsert(device)
        except (KeyError, ValueError) as exc:
            logger.warning("invalid device config %r: %s", raw.get("device_id"), exc)

    # ── NATS device-update subscriptions ─────────────────────────────────────
    async def on_device_upsert(msg):
        try:
            device = Device.from_dict(json.loads(msg.data))
            poller.upsert(device)
            logger.info("device upserted via NATS: %s", device.device_id)
        except Exception as exc:
            logger.error("bad device upsert payload: %s", exc)

    async def on_device_remove(msg):
        try:
            payload = json.loads(msg.data)
            device_id = payload["device_id"]
            poller.remove(device_id)
            logger.info("device removed via NATS: %s", device_id)
        except Exception as exc:
            logger.error("bad device remove payload: %s", exc)

    await publisher.subscribe("netpulse.devices.upsert", on_device_upsert)
    await publisher.subscribe("netpulse.devices.remove", on_device_remove)

    # ── Trap receiver ─────────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    try:
        trap_transport, _ = await loop.create_datagram_endpoint(
            lambda: SNMPTrapReceiver(publisher),
            local_addr=(cfg.host, cfg.trap_port),
        )
    except PermissionError:
        logger.error(
            "Cannot bind UDP port %d (needs NET_BIND_SERVICE or port > 1024). "
            "Set SNMP_TRAP_PORT in .env to a high port for local dev.",
            cfg.trap_port,
        )
        raise

    logger.info(
        "ingest-snmp running — trap UDP %s:%d, polling %d device(s)",
        cfg.host, cfg.trap_port, len(cfg.load_devices()),
    )

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutdown signal received")
    poller.stop_all()
    trap_transport.close()
    if gnmi_activity is not None:
        await gnmi_activity.close()
    await publisher.drain()
    logger.info("ingest-snmp stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

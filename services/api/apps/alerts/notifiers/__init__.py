"""
Pluggable notifier registry.

A notifier turns an ``AlertPayload`` into a delivery on one channel type. Adding
a new channel type = writing a ``Notifier`` subclass and decorating it with
``@register("<channel_type>")`` — the dispatcher looks it up by
``AlertChannel.channel_type``. Each ``send`` returns ``(ok, detail)`` and must
never raise (the dispatcher relies on that for per-channel isolation).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, "Notifier"] = {}


class Notifier:
    """Base class. Subclasses implement ``send(channel, payload) -> (ok, detail)``."""

    channel_type: str = ""

    def send(self, channel, payload) -> tuple[bool, str]:  # pragma: no cover - interface
        raise NotImplementedError


def register(channel_type: str):
    def _wrap(cls):
        cls.channel_type = channel_type
        _REGISTRY[channel_type] = cls()
        return cls
    return _wrap


def get_notifier(channel_type: str) -> Notifier | None:
    return _REGISTRY.get(channel_type)


def registered_types() -> list[str]:
    return sorted(_REGISTRY.keys())


def _load_builtin_notifiers() -> None:
    """Import the built-in notifier modules so their @register runs once."""
    from . import email, pagerduty, slack, teams, webhook  # noqa: F401


_load_builtin_notifiers()

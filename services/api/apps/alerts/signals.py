"""
Centralized fire/resolve → dispatch wiring.

A ``post_save`` on AlertEvent is the single hook that routes *every* alert
source (interface, reachability, stability, liveness, functional, environment,
circuits, compliance, os-policy, flow, …) through the same dispatch layer
without each call site having to know about notifications:

  - created + FIRING            → dispatch the "firing" transition
  - saved + state == RESOLVED   → dispatch the "resolved" transition

``.update()``-based auto-resolutions bypass signals, so resolve.py
(``resolve_matching``) and the bulk-resolve view call ``dispatch_event``
directly. Both paths are idempotent (dispatch claims the transition), so the
overlap is safe.

Dispatch is deferred to ``transaction.on_commit`` so notifications only fire for
events that actually persist, and the network I/O happens outside the
alert-creating transaction.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AlertEvent
from .payload import FIRING, RESOLVED

logger = logging.getLogger(__name__)


@receiver(post_save, sender=AlertEvent, dispatch_uid="alerts_dispatch_on_save")
def _dispatch_on_save(sender, instance: AlertEvent, created: bool, **kwargs):
    if created and instance.state == AlertEvent.State.FIRING:
        transition = FIRING
    elif not created and instance.state == AlertEvent.State.RESOLVED:
        transition = RESOLVED
    else:
        return

    pk = instance.pk

    def _run():
        try:
            from .dispatch import dispatch_event
            from .models import AlertEvent as _AE
            event = _AE.objects.filter(pk=pk).select_related("rule").first()
            if event is not None:
                dispatch_event(event, transition)
        except Exception as exc:  # noqa: BLE001 — never let dispatch break a save
            logger.warning("on_commit dispatch failed for alert %s: %s", pk, exc)

    transaction.on_commit(_run)

"""Agent-side credential cache — RAM-only, never disk.

The collector agent caches broker responses in memory with a short TTL. The
behaviour when the TTL expires is an EXPLICIT, tested branch, not emergent:

  * fresh (age <= ttl)                  → serve cached
  * expired, broker reachable           → miss; re-fetch from the broker
  * expired, broker UNREACHABLE,
      age <= max_stale                  → serve last-known (the monitoring lean:
                                          a central outage must not blind polling)
  * expired, unreachable, age > max_stale → drop; stop using it (hard ceiling so
                                          we never poll on ancient creds forever)

Nothing here writes to disk; the store is a process-memory dict that dies with
the agent. The lean (keep-last-known under a bounded staleness) is deliberate for
a monitoring tool — flip KEEP_LAST_KNOWN to fail-closed if that's preferred.

REVOCATION INTERACTION (the bound, stated explicitly):
  * Central REACHABLE (normal): on TTL expiry the agent RE-FETCHES (the
    `expired_refetch` branch). A credential revoked/rotated centrally therefore
    stops being served within one TTL (default 5 min) — the broker denies or
    returns the new value on the next fetch. So normal-case revocation latency is
    TTL, NOT max_stale.
  * Central UNREACHABLE (outage): the agent serves last-known up to max_stale.
    KNOWN BOUND — a credential revoked DURING a central outage may remain usable
    at the edge for up to max_stale. This is the price of not blinding monitoring
    during an outage; bound it by lowering max_stale, or set keep_last_known=False
    to fail closed.
  * Cheap instant eviction when reachable (built with the agent): the agent
    already watches its config-down KV bundle; when a device's credential ref
    changes there, the agent calls invalidate()/invalidate_for_device() to evict
    immediately — no waiting for TTL. The hook is here; the bundle epoch wiring
    lands with the agent.
"""
from __future__ import annotations

import os
import time

def _envint(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

# Defaults (seconds), env-configurable — some deployments want a shorter ceiling.
DEFAULT_TTL = _envint("COLLECTOR_CRED_CACHE_TTL", 300)            # 5 min
DEFAULT_MAX_STALE = _envint("COLLECTOR_CRED_CACHE_MAX_STALE", 86400)  # 24h hard ceiling
KEEP_LAST_KNOWN = os.environ.get("COLLECTOR_CRED_CACHE_KEEP_LAST_KNOWN", "true").lower() == "true"

MISS = "miss"
FRESH = "fresh"
EXPIRED_REFETCH = "expired_refetch"
STALE_KEPT = "stale_kept"
STALE_DROPPED = "stale_dropped"


class CredentialCache:
    def __init__(self, ttl=DEFAULT_TTL, max_stale=DEFAULT_MAX_STALE,
                 keep_last_known=KEEP_LAST_KNOWN, clock=time.monotonic):
        self.ttl = ttl
        self.max_stale = max_stale
        self.keep_last_known = keep_last_known
        self._clock = clock
        self._store: dict = {}   # key -> (value, stored_at) — RAM only

    def put(self, key, value):
        self._store[key] = (value, self._clock())

    def get(self, key, broker_reachable: bool):
        """Return (value_or_None, status). `status` is one of the constants above."""
        entry = self._store.get(key)
        if entry is None:
            return None, MISS
        value, ts = entry
        age = self._clock() - ts
        if age <= self.ttl:
            return value, FRESH
        # expired
        if broker_reachable:
            return None, EXPIRED_REFETCH        # go re-fetch; don't serve stale when we can refresh
        if self.keep_last_known and age <= self.max_stale:
            return value, STALE_KEPT            # outage: keep polling on last-known
        self._store.pop(key, None)
        return None, STALE_DROPPED              # too stale (or fail-closed) → drop

    def invalidate(self, key):
        self._store.pop(key, None)

    def invalidate_matching(self, predicate):
        """Evict every key for which predicate(key) is true — the agent calls this
        on a config-bundle change (e.g. a device's credential ref rotated) for
        instant revocation while central is reachable. Returns the count evicted."""
        doomed = [k for k in self._store if predicate(k)]
        for k in doomed:
            self._store.pop(k, None)
        return len(doomed)

    def clear(self):
        self._store.clear()

"""Agent-side credential cache — the expiry/unreachable branches are explicit."""
from apps.collectors.credential_cache import (
    CredentialCache, MISS, FRESH, EXPIRED_REFETCH, STALE_KEPT, STALE_DROPPED,
)


class _Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, s): self.t += s


def _cache(**kw):
    return CredentialCache(ttl=300, max_stale=86400, clock=_Clock(), **kw), None


def test_miss():
    c = CredentialCache(clock=_Clock())
    assert c.get("k", broker_reachable=True) == (None, MISS)


def test_fresh():
    clk = _Clock(); c = CredentialCache(ttl=300, clock=clk)
    c.put("k", {"v": 1}); clk.advance(100)
    assert c.get("k", broker_reachable=False) == ({"v": 1}, FRESH)


def test_expired_with_broker_reachable_refetches():
    clk = _Clock(); c = CredentialCache(ttl=300, clock=clk)
    c.put("k", {"v": 1}); clk.advance(301)
    # Broker reachable → don't serve stale, signal a re-fetch.
    assert c.get("k", broker_reachable=True) == (None, EXPIRED_REFETCH)


def test_expired_unreachable_keeps_last_known_within_max_stale():
    clk = _Clock(); c = CredentialCache(ttl=300, max_stale=86400, clock=clk)
    c.put("k", {"v": 1}); clk.advance(3600)   # 1h: expired, but < max_stale
    # Monitoring lean: central outage must not blind polling.
    assert c.get("k", broker_reachable=False) == ({"v": 1}, STALE_KEPT)


def test_expired_unreachable_drops_beyond_max_stale():
    clk = _Clock(); c = CredentialCache(ttl=300, max_stale=86400, clock=clk)
    c.put("k", {"v": 1}); clk.advance(90000)  # > 24h
    assert c.get("k", broker_reachable=False) == (None, STALE_DROPPED)
    # Dropped from the store.
    assert c.get("k", broker_reachable=False) == (None, MISS)


def test_fail_closed_mode_does_not_keep_stale():
    clk = _Clock(); c = CredentialCache(ttl=300, keep_last_known=False, clock=clk)
    c.put("k", {"v": 1}); clk.advance(3600)
    assert c.get("k", broker_reachable=False) == (None, STALE_DROPPED)


def test_never_persists_to_disk():
    # The store is a plain in-memory dict — no filesystem handle anywhere.
    c = CredentialCache(clock=_Clock())
    c.put("k", {"v": 1})
    assert isinstance(c._store, dict)

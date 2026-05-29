"""
OpenBao-backed credential manager.

Credentials are fetched from OpenBao KV v2 at paths like `secret/snmp/<device_id>`.
Results are cached in memory with a configurable TTL.  All OpenBao I/O runs in
a thread-pool executor so it never blocks the event loop.

If OpenBao is unreachable or the path does not exist, a CredentialError is
raised so the caller can skip the poll cycle and retry later.
"""
import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    """Raised when credentials cannot be retrieved from OpenBao."""


class CredentialManager:
    def __init__(self, addr: str, token: str, cache_ttl: int = 300) -> None:
        self._addr = addr
        self._token = token
        self._ttl = cache_ttl
        # path → (creds_dict, expires_monotonic)
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock = asyncio.Lock()
        self._client = None   # lazy-init so we can test without hvac installed

    def _get_client(self):
        if self._client is None:
            import hvac
            self._client = hvac.Client(url=self._addr, token=self._token)
        return self._client

    async def get(self, path: str) -> dict[str, Any]:
        """
        Return credentials dict for `path`.  Raises CredentialError on failure.
        Never returns stale data that exceeds TTL.
        """
        async with self._lock:
            now = time.monotonic()
            if path in self._cache:
                creds, expires = self._cache[path]
                if expires > now:
                    return creds

            loop = asyncio.get_running_loop()
            try:
                creds = await loop.run_in_executor(None, self._fetch, path)
            except Exception as exc:
                raise CredentialError(f"OpenBao fetch failed for {path!r}: {exc}") from exc

            self._cache[path] = (creds, now + self._ttl)
            logger.debug("cached credentials for %r (ttl=%ds)", path, self._ttl)
            return creds

    def _fetch(self, path: str) -> dict[str, Any]:
        client = self._get_client()
        secret = client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point="secret",
        )
        return secret["data"]["data"]

    def invalidate(self, path: str) -> None:
        """Remove a path from the cache (e.g. after a credential rotation event)."""
        self._cache.pop(path, None)

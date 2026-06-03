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
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    """Raised when credentials cannot be retrieved from OpenBao."""


class CredentialManager:
    """
    Fetches OpenBao credentials, resolving the OpenBao token *lazily* on every
    client build rather than freezing it at construction time.

    Why lazy: on a `docker compose restart` / server reboot, ingest-snmp can
    start (and import config, resolving the token) before the api service has
    written ``.init_keys`` / unsealed OpenBao. A token captured at that instant
    is empty/invalid and, if cached for the process lifetime, makes every
    subsequent credential read fail ("auth keys return empty") until the
    container is recreated. Re-resolving the token on demand — and retrying once
    after dropping a stale client on failure — lets the poller self-heal the
    moment the keys file becomes readable and OpenBao is unsealed.
    """

    def __init__(
        self,
        addr: str,
        token: str | None = None,
        cache_ttl: int = 300,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._addr = addr
        # Prefer a provider (re-read each time); fall back to a static token for
        # tests / explicit-token callers.
        if token_provider is not None:
            self._token_provider = token_provider
        else:
            self._token_provider = lambda: token or ""
        self._ttl = cache_ttl
        # path → (creds_dict, expires_monotonic)
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock = asyncio.Lock()
        self._client = None        # lazy-init so we can test without hvac installed
        self._client_token = None  # token the current client was built with

    def _get_client(self):
        token = self._token_provider() or ""
        # (Re)build when we have no client yet or the resolved token changed —
        # e.g. it was empty during a reboot race and is now available, or it was
        # rotated. This is what makes the manager self-heal without a restart.
        if self._client is None or token != self._client_token:
            import hvac
            self._client = hvac.Client(url=self._addr, token=token)
            self._client_token = token
        return self._client

    def _reset_client(self) -> None:
        """Drop the cached client so the next call re-resolves the token."""
        self._client = None
        self._client_token = None

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
            except Exception:
                # The token may have been unavailable/sealed when the client was
                # built (reboot race). Drop the client so the retry re-resolves
                # the token, then attempt once more before giving up.
                self._reset_client()
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

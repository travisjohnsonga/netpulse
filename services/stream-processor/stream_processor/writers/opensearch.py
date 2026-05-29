"""Async OpenSearch bulk writer with configurable batch size and timeout."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _monthly_index(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y.%m')}"


class OpenSearchWriter:
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        batch_size: int = 100,
        batch_timeout: float = 5.0,
    ) -> None:
        self._batch_size = batch_size
        self._batch_timeout = batch_timeout
        self._queue: list[dict] = []
        self._last_flush = time.monotonic()
        self._client = None
        try:
            from opensearchpy import AsyncOpenSearch
            auth = (user, password) if password else None
            self._client = AsyncOpenSearch(
                hosts=[url], http_auth=auth,
                verify_certs=False, ssl_show_warn=False,
            )
            logger.info("OpenSearch connected: %s", url)
        except Exception as exc:
            logger.warning("OpenSearch unavailable — writes disabled: %s", exc)

    @property
    def available(self) -> bool:
        return self._client is not None

    async def index(self, index: str, doc: dict) -> None:
        """Queue a document; flush when batch is full or timeout elapsed."""
        self._queue.append({"_index": index, "doc": doc})
        if (
            len(self._queue) >= self._batch_size
            or (time.monotonic() - self._last_flush) >= self._batch_timeout
        ):
            await self.flush()

    async def flush(self) -> None:
        if not self._client or not self._queue:
            self._queue = []
            self._last_flush = time.monotonic()
            return
        batch, self._queue = self._queue, []
        self._last_flush = time.monotonic()
        body: list = []
        for item in batch:
            body.append({"index": {"_index": item["_index"]}})
            body.append(item["doc"])
        try:
            await self._client.bulk(body=body)
            logger.debug("OpenSearch bulk flush: %d docs", len(batch))
        except Exception as exc:
            logger.error("OpenSearch bulk flush failed: %s", exc)

    async def close(self) -> None:
        await self.flush()
        if self._client:
            await self._client.close()

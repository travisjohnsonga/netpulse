"""Tests for the OpenSearch bulk writer (without a live server)."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from stream_processor.writers.opensearch import OpenSearchWriter, _monthly_index


class TestMonthlyIndex:
    def test_format(self):
        idx = _monthly_index("netpulse-flows")
        import re
        assert re.match(r"netpulse-flows-\d{4}\.\d{2}", idx)

    def test_prefix_preserved(self):
        assert _monthly_index("netpulse-traps").startswith("netpulse-traps-")


class TestOpenSearchWriter:
    def _writer(self, batch_size=100, batch_timeout=5.0):
        w = OpenSearchWriter.__new__(OpenSearchWriter)
        w._batch_size = batch_size
        w._batch_timeout = batch_timeout
        w._queue = []
        w._last_flush = time.monotonic()
        w._client = AsyncMock()
        return w

    def test_available_true_when_client_set(self):
        w = self._writer()
        assert w.available is True

    def test_available_false_when_no_client(self):
        w = self._writer()
        w._client = None
        assert w.available is False

    def test_index_queues_document(self):
        w = self._writer(batch_size=100, batch_timeout=9999.0)
        asyncio.run(w.index("my-index", {"key": "val"}))
        assert len(w._queue) == 1
        assert w._queue[0]["_index"] == "my-index"

    def test_flush_triggered_when_batch_full(self):
        w = self._writer(batch_size=3, batch_timeout=9999.0)
        w._client.bulk = AsyncMock()
        asyncio.run(w.index("idx", {"a": 1}))
        asyncio.run(w.index("idx", {"b": 2}))
        asyncio.run(w.index("idx", {"c": 3}))  # triggers flush
        w._client.bulk.assert_called_once()
        assert len(w._queue) == 0

    def test_flush_triggered_on_timeout(self):
        w = self._writer(batch_size=100, batch_timeout=0.001)
        w._client.bulk = AsyncMock()
        asyncio.run(w.index("idx", {"x": 1}))
        import time as _time; _time.sleep(0.01)
        asyncio.run(w.index("idx", {"y": 2}))
        # Second index triggered flush of first item
        w._client.bulk.assert_called_once()

    def test_flush_sends_bulk_body(self):
        w = self._writer(batch_size=100, batch_timeout=9999.0)
        w._client.bulk = AsyncMock()
        w._queue = [{"_index": "test", "doc": {"hello": "world"}}]
        asyncio.run(w.flush())
        args = w._client.bulk.call_args[1]["body"]
        assert args[0] == {"index": {"_index": "test"}}
        assert args[1] == {"hello": "world"}

    def test_flush_clears_queue(self):
        w = self._writer()
        w._client.bulk = AsyncMock()
        w._queue = [{"_index": "x", "doc": {}}]
        asyncio.run(w.flush())
        assert w._queue == []

    def test_flush_empty_queue_no_client_call(self):
        w = self._writer()
        w._client.bulk = AsyncMock()
        asyncio.run(w.flush())
        w._client.bulk.assert_not_called()

    def test_flush_no_client_clears_queue(self):
        w = self._writer()
        w._client = None
        w._queue = [{"_index": "x", "doc": {}}]
        asyncio.run(w.flush())
        assert w._queue == []

    def test_bulk_error_does_not_raise(self):
        w = self._writer()
        w._client.bulk = AsyncMock(side_effect=Exception("connection refused"))
        w._queue = [{"_index": "x", "doc": {}}]
        asyncio.run(w.flush())  # should not raise

    def test_close_flushes_and_closes_client(self):
        w = self._writer()
        w._client.bulk = AsyncMock()
        w._client.close = AsyncMock()
        w._queue = [{"_index": "x", "doc": {"k": "v"}}]
        asyncio.run(w.close())
        w._client.bulk.assert_called_once()
        w._client.close.assert_called_once()

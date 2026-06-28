"""Stream-processor OpenSearch resilience: ack-after-write / NAK-on-failure.

The buffered OpenSearch path (logs + flows) must ack a JetStream message ONLY
after its doc is durably written, and NAK (redeliver) on failure — so an
OpenSearch blip can't drop security logs (acked-but-not-written). These tests
drive _flush_locked / _handle_flush_failure / _on_log with fakes (no live
NATS/OpenSearch) and assert the ack/nak/term fate.
"""
import asyncio


class FakeMsg:
    def __init__(self, subject="netpulse.logs.auth.host", data=b"{}"):
        self.subject = subject
        self.data = data
        self.acks = self.naks = self.terms = 0
        self.nak_delay = None

    async def ack(self):
        self.acks += 1

    async def nak(self, delay=None):
        self.naks += 1
        self.nak_delay = delay

    async def term(self):
        self.terms += 1


class FakeOS:
    """Minimal async OpenSearch stub. mode: ok | raise | partial."""
    def __init__(self, mode="ok", fail_indices=None):
        self.mode = mode
        self.fail_indices = set(fail_indices or [])
        self.calls = 0

    async def bulk(self, body):
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("opensearch down")
        n = len(body) // 2  # [action, doc] pairs
        if self.mode == "ok":
            return {"errors": False, "items": [{"index": {"status": 201}} for _ in range(n)]}
        items = []
        for i in range(n):
            if i in self.fail_indices:
                items.append({"index": {"status": 503, "error": {"type": "es_rejected_execution"}}})
            else:
                items.append({"index": {"status": 201}})
        return {"errors": bool(self.fail_indices), "items": items}


def _proc(os_client):
    from apps.telemetry.management.commands.run_stream_processor import Command
    cmd = Command()
    cmd._os_buffer = []
    cmd._os_buffer_lock = asyncio.Lock()
    cmd._os_client = os_client
    return cmd


def test_success_acks_all_no_redeliver():
    cmd = _proc(FakeOS("ok"))
    m1, m2 = FakeMsg(), FakeMsg()
    cmd._os_buffer = [("i", {"a": 1}, m1), ("i", {"b": 2}, m2)]
    asyncio.run(cmd._os_flush_now())
    assert (m1.acks, m2.acks) == (1, 1)
    assert (m1.naks, m2.naks) == (0, 0)
    assert cmd._os_buffer == []


def test_total_failure_naks_for_redelivery_never_acks():
    # OpenSearch down → messages must NOT be acked (no acked-but-not-written
    # loss); they are NAK'd so JetStream redelivers them.
    from apps.telemetry.management.commands.run_stream_processor import _REDELIVER_DELAY_S
    cmd = _proc(FakeOS("raise"))
    m1, m2 = FakeMsg(), FakeMsg()
    cmd._os_buffer = [("i", {"a": 1}, m1), ("i", {"b": 2}, m2)]
    asyncio.run(cmd._os_flush_now())
    assert (m1.acks, m2.acks) == (0, 0)
    assert (m1.naks, m2.naks) == (1, 1)
    assert m1.nak_delay == _REDELIVER_DELAY_S
    # msg-bearing entries are redelivered by JetStream, not re-buffered.
    assert cmd._os_buffer == []


def test_total_failure_requeues_msgless_entries():
    # Trap/otel docs have no msg (can't redeliver) → re-buffered for retry.
    cmd = _proc(FakeOS("raise"))
    cmd._os_buffer = [("i", {"a": 1}, None)]
    asyncio.run(cmd._os_flush_now())
    assert len(cmd._os_buffer) == 1


def test_partial_errors_ack_ok_nak_failed():
    cmd = _proc(FakeOS("partial", fail_indices={1}))
    m0, m1, m2 = FakeMsg(), FakeMsg(), FakeMsg()
    cmd._os_buffer = [("i", {}, m0), ("i", {}, m1), ("i", {}, m2)]
    asyncio.run(cmd._os_flush_now())
    assert (m0.acks, m2.acks) == (1, 1)          # succeeded → acked
    assert (m1.acks, m1.naks) == (0, 1)          # failed item → redelivered, not acked
    assert cmd._os_buffer == []


def test_unconfigured_client_acks_and_drops():
    cmd = _proc(None)
    m = FakeMsg()
    cmd._os_buffer = [("i", {}, m)]
    asyncio.run(cmd._os_flush_now())
    assert m.acks == 1 and cmd._os_buffer == []  # can't store → drop, don't loop


def test_on_log_terms_poison_message():
    cmd = _proc(FakeOS("ok"))
    m = FakeMsg(subject="netpulse.logs.x", data=b"not-json{")
    asyncio.run(cmd._on_log(m))
    assert m.terms == 1            # unprocessable → terminated (no redelivery loop)
    assert cmd._os_buffer == []    # nothing buffered


def test_on_log_buffers_with_msg_for_ack_after_write():
    cmd = _proc(FakeOS("ok"))

    async def _noop(*a, **k):
        return None
    cmd._inspect_log_security = _noop
    m = FakeMsg(subject="netpulse.logs.auth.myhost", data=b'{"message":"hello"}')
    asyncio.run(cmd._on_log(m))
    assert len(cmd._os_buffer) == 1
    index, doc, msg = cmd._os_buffer[0]
    assert msg is m                       # the buffer owns the ack
    assert doc["message"] == "hello"
    assert doc["source"] == "auth"        # parts[2] of the subject
    assert m.acks == 0 and m.terms == 0   # not acked until flush succeeds

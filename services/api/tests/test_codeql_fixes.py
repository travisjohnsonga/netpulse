"""Regression tests for the CodeQL alerts fixed on this branch.

P1 path traversal (mibs), P2 cleartext secret logging (audit), P3 information
exposure via exception (compliance serializer + the shared safe_detail helper
used by the integrations views).
"""
import logging
from pathlib import Path

import pytest


# ── P1: path traversal (#26 mibs/index.py) ───────────────────────────────────

class TestMibPathTraversal:
    @pytest.mark.parametrize("bad", [
        "../../etc/passwd.mib", "/etc/passwd", "sub/dir/x.mib", "..", ".",
        "x/../y.mib", "\x00.mib", "..%2f..%2fx.mib", "  ../x.mib  ",
    ])
    def test_rejects_unsafe_names(self, tmp_path, bad):
        from apps.mibs.index import safe_mib_path
        with pytest.raises(ValueError):
            safe_mib_path(tmp_path, bad)

    def test_accepts_plain_filename_inside_base(self, tmp_path):
        from apps.mibs.index import safe_mib_path
        p = safe_mib_path(tmp_path, "CISCO-SMI.my")
        assert Path(p).name == "CISCO-SMI.my"
        assert Path(p).parent == Path(tmp_path).resolve()

    def test_save_upload_rejects_traversal(self, tmp_path, monkeypatch):
        from apps.mibs import index
        monkeypatch.setattr(index, "mibs_dir", lambda: tmp_path)
        r = index.save_upload("../../evil.mib", "WHATEVER-CONTENT")
        assert r["ok"] is False and r["error"] == "invalid MIB filename"
        # Nothing escaped the base dir.
        assert not (tmp_path.parent / "evil.mib").exists()


# ── P2: cleartext logging of secrets (#33 core/audit.py) ─────────────────────

@pytest.mark.django_db
def test_audit_failure_does_not_log_field_values(caplog, monkeypatch):
    from apps.core import audit as audit_mod
    from apps.core.models import AuditLog

    secret = "PLAINTEXT-CRED-do-not-log-xyz"

    def boom(**kwargs):  # a DB driver echoing the inserted (secret-bearing) field
        raise ValueError(f"insert failed echoing: {kwargs.get('description')}")

    monkeypatch.setattr(AuditLog.objects, "create", boom)
    caplog.set_level(logging.WARNING)
    result = audit_mod.log_event(AuditLog.EventType.CREDENTIAL_ACCESSED, description=secret)
    assert result is None                       # auditing never raises
    assert secret not in caplog.text            # the secret never reaches the log
    # The failure is recorded with a static marker only — no exception object or
    # field value is logged (CodeQL #34).
    assert "audit log_event failed" in caplog.text
    # The event type is re-derived from the canonical enum, not the raw input
    # (CodeQL #37) — for a known event the value still appears for triage.
    assert "credential_accessed" in caplog.text


@pytest.mark.django_db
def test_audit_failure_logs_canonical_event_only(caplog, monkeypatch):
    """An unrecognised event_type must fall back to a constant, never be logged
    verbatim (CodeQL #37 — the logged token must be a trusted constant)."""
    from apps.core import audit as audit_mod
    from apps.core.models import AuditLog

    def boom(**kwargs):
        raise ValueError("db down")

    monkeypatch.setattr(AuditLog.objects, "create", boom)
    caplog.set_level(logging.WARNING)
    bogus = "not-a-real-event-type-sensitive?"
    result = audit_mod.log_event(bogus)
    assert result is None
    assert bogus not in caplog.text
    assert "event_type=<unknown>" in caplog.text


# ── P3: information exposure via exception ────────────────────────────────────

def test_safe_detail_returns_generic_not_exception():
    from apps.core.errors import safe_detail
    exc = ValueError("INTERNAL /srv/app/secrets.py line 42 boom")
    msg = safe_detail(exc, context="unit", public="Could not connect.")
    assert msg == "Could not connect."
    assert "INTERNAL" not in msg and "secrets.py" not in msg


@pytest.mark.django_db
def test_invalid_regex_returns_generic_message():
    from apps.compliance.serializers import ApprovedOSVersionSerializer
    ser = ApprovedOSVersionSerializer(data={
        "platform": "ios_xe", "is_regex": True,
        "version_pattern": "(unclosed[group", "status": "approved",
    })
    assert not ser.is_valid()
    msg = str(ser.errors["version_pattern"][0])
    assert msg == "Invalid regular expression."     # generic, not the raw re.error
    assert "unbalanced" not in msg.lower() and "position" not in msg.lower()

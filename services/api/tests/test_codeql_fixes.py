"""Regression tests for the CodeQL alerts fixed on this branch.

P1 path traversal (mibs), P2 cleartext secret logging (audit), P3 information
exposure via exception (compliance serializer + the shared safe_detail helper
used by the integrations views).

The ``TestNoExceptionExposure`` guard at the bottom is the *durable* fix for the
recurring "information exposure through an exception" alerts (#40 devices,
#43/#44 frameworks, and predecessors): instead of chasing each new offending
file by hand, it statically asserts that NO view under ``apps/`` returns the raw
exception to a client. Any future regression fails CI here, not in a Dependabot
scan weeks later.
"""
import ast
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


# ── P3 (durable): no view may leak exception text to an HTTP response ─────────
#
# CodeQL "py/stack-trace-exposure" keeps re-firing because each new view that
# does `except Exception as exc: return Response({"error": str(exc)})` is a fresh
# alert. This guard makes the rule enforceable in CI: it parses every module
# under apps/ and flags any except-handler whose `return Response(...)` (or DRF
# `raise ValidationError(...)`) references the bound exception variable *unless*
# it is funnelled through an approved sanitizer that emits only a static string.

_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"
_RESPONSE_SINKS = {"Response", "JsonResponse", "HttpResponse"}
_RAISE_SINKS = {
    "ValidationError", "APIException", "ParseError", "NotFound",
    "PermissionDenied", "NotAuthenticated", "Throttled",
}
# Calls that log the exception server-side and return ONLY a constant message.
_SAFE_WRAPPERS = {"safe_detail", "internal_error_response", "log_internal_error"}


def _called_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _raw_exc_escapes(arg_nodes, var: str) -> bool:
    """True if ``var`` is referenced in ``arg_nodes`` outside a safe wrapper call.

    A safe-wrapper subtree (e.g. ``safe_detail(exc, ...)``) is NOT descended into —
    its body is guaranteed static — so passing the exception there is allowed.
    """
    found = False

    class _V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            if _called_name(node) in _SAFE_WRAPPERS:
                return  # sanitized: do not descend into the arguments
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name):
            nonlocal found
            if node.id == var:
                found = True

    visitor = _V()
    for node in arg_nodes:
        visitor.visit(node)
    return found


def _handler_violations(handler: ast.ExceptHandler) -> list[int]:
    var = handler.name
    if not var:
        return []  # bare `except:` / `except Exc:` binds nothing to leak
    out: list[int] = []
    for node in ast.walk(handler):
        call = None
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            if _called_name(node.value) in _RESPONSE_SINKS:
                call = node.value
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if _called_name(node.exc) in _RAISE_SINKS:
                call = node.exc
        if call is not None:
            args = list(call.args) + [kw.value for kw in call.keywords]
            if _raw_exc_escapes(args, var):
                out.append(node.lineno)
    return out


class TestNoExceptionExposure:
    def test_no_view_returns_raw_exception(self):
        offenders: list[str] = []
        for py in _APPS_DIR.rglob("*.py"):
            parts = set(py.parts)
            if parts & {"migrations", "__pycache__", "tests"}:
                continue
            tree = ast.parse(py.read_text(), filename=str(py))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    for lineno in _handler_violations(node):
                        offenders.append(f"{py.relative_to(_APPS_DIR.parent)}:{lineno}")
        assert not offenders, (
            "Exception text must never reach an HTTP response body — route it "
            "through safe_detail()/internal_error_response() (CodeQL "
            "py/stack-trace-exposure). Offending sinks:\n  "
            + "\n  ".join(sorted(offenders))
        )

    def test_guard_catches_a_planted_leak(self):
        """Sanity-check the guard itself: a raw `return Response(str(exc))` is
        detected, while the safe_detail() form is not."""
        bad = ast.parse(
            "def v():\n"
            "    try:\n        f()\n"
            "    except Exception as exc:\n"
            "        return Response({'error': str(exc)})\n")
        good = ast.parse(
            "def v():\n"
            "    try:\n        f()\n"
            "    except Exception as exc:\n"
            "        return Response({'error': safe_detail(exc, logger, 'v')})\n")
        bad_h = [n for n in ast.walk(bad) if isinstance(n, ast.ExceptHandler)][0]
        good_h = [n for n in ast.walk(good) if isinstance(n, ast.ExceptHandler)][0]
        assert _handler_violations(bad_h)        # leak detected
        assert not _handler_violations(good_h)   # sanitized form passes

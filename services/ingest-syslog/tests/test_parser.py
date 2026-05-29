"""
Tests for ingest.parser — RFC 3164 and RFC 5424 syslog parsing.
"""
import re
from datetime import datetime

import pytest

from ingest.parser import FACILITIES, SEVERITIES, _parse_sd, parse


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse(raw: str, *, ip: str = "10.0.0.1", port: int = 514, transport: str = "udp") -> dict:
    return parse(raw.encode(), ip, port, transport)


def _assert_facility_severity(msg: dict, facility: int, severity: int) -> None:
    assert msg["facility"] == facility
    assert msg["facility_name"] == FACILITIES[facility]
    assert msg["severity"] == severity
    assert msg["severity_name"] == SEVERITIES[severity]


# ── Priority / facility / severity maths ─────────────────────────────────────

class TestPriority:
    def test_kern_emerg(self):
        # PRI=0 → facility=0 (kern), severity=0 (emerg)
        msg = _parse("<0>Oct 11 22:14:15 host test: msg")
        _assert_facility_severity(msg, 0, 0)

    def test_auth_info(self):
        # PRI=38 → facility=4 (auth), severity=6 (info)  4*8+6=38
        msg = _parse("<38>Oct 11 22:14:15 host sshd[99]: Accepted password")
        _assert_facility_severity(msg, 4, 6)

    def test_local7_debug(self):
        # PRI=191 → facility=23 (local7), severity=7 (debug)  23*8+7=191
        msg = _parse("<191>Oct 11 22:14:15 host app: debug")
        _assert_facility_severity(msg, 23, 7)

    def test_source_metadata_attached(self):
        msg = _parse("<13>Oct 1 00:00:00 h prog: m", ip="192.168.1.50", port=1234, transport="tcp")
        assert msg["source_ip"] == "192.168.1.50"
        assert msg["source_port"] == 1234
        assert msg["transport"] == "tcp"

    def test_received_at_is_iso(self):
        msg = _parse("<13>Oct 1 00:00:00 h p: m")
        # Should be parseable as ISO 8601
        datetime.fromisoformat(msg["received_at"])

    def test_raw_preserved(self):
        raw = "<13>Oct 11 22:14:15 router1 sshd: hello"
        msg = _parse(raw)
        assert msg["raw"] == raw


# ── RFC 5424 ──────────────────────────────────────────────────────────────────

class TestRFC5424:
    # RFC 5424 §6.5 example 1
    _EX1 = (
        "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su - ID47 - "
        "\xef\xbb\xbf'su root' failed for lonvick on /dev/pts/8"
    )

    def test_version_detected(self):
        assert _parse(self._EX1)["version"] == 1

    def test_facility_severity(self):
        # PRI=34 → facility=4 (auth), severity=2 (crit)
        msg = _parse(self._EX1)
        _assert_facility_severity(msg, 4, 2)

    def test_hostname(self):
        assert _parse(self._EX1)["hostname"] == "mymachine.example.com"

    def test_app_name(self):
        assert _parse(self._EX1)["app_name"] == "su"

    def test_proc_id_nilvalue(self):
        assert _parse(self._EX1)["proc_id"] is None

    def test_msg_id(self):
        assert _parse(self._EX1)["msg_id"] == "ID47"

    def test_no_sd(self):
        assert _parse(self._EX1)["structured_data"] == {}

    def test_message_bom_stripped(self):
        msg = _parse(self._EX1)
        assert not msg["message"].startswith("\xef\xbb\xbf")
        assert "failed for lonvick" in msg["message"]

    def test_timestamp_preserved(self):
        msg = _parse(self._EX1)
        assert msg["timestamp"] == "2003-10-11T22:14:15.003Z"

    def test_timestamp_nilvalue(self):
        raw = "<165>1 - 192.0.2.1 myproc 8710 - - hello"
        assert _parse(raw)["timestamp"] is None

    def test_timezone_offset(self):
        raw = "<165>1 2003-08-24T05:14:15.000003-07:00 192.0.2.1 myproc 8710 - - %%msg"
        msg = _parse(raw)
        assert msg["timestamp"] == "2003-08-24T05:14:15.000003-07:00"

    def test_nilvalue_hostname_falls_back_to_source_ip(self):
        raw = "<34>1 2003-10-11T22:14:15Z - su - - - test"
        msg = _parse(raw, ip="1.2.3.4")
        assert msg["hostname"] == "1.2.3.4"

    def test_all_nilvalues(self):
        raw = "<0>1 - - - - - - empty"
        msg = _parse(raw)
        assert msg["hostname"] is not None  # falls back to source_ip
        assert msg["app_name"] is None
        assert msg["proc_id"] is None
        assert msg["msg_id"] is None
        assert msg["structured_data"] == {}

    def test_empty_message(self):
        raw = "<34>1 2003-10-11T22:14:15Z host app 123 ID1 -"
        msg = _parse(raw)
        assert msg["message"] == ""


# ── RFC 5424 structured data ──────────────────────────────────────────────────

class TestRFC5424StructuredData:
    def test_single_element(self):
        raw = (
            '<165>1 2003-08-24T05:14:15Z 192.0.2.1 myproc 8710 - '
            '[exampleSDID@32473 iut="3" eventSource="Application"] An event'
        )
        msg = _parse(raw)
        sd = msg["structured_data"]
        assert "exampleSDID@32473" in sd
        assert sd["exampleSDID@32473"]["iut"] == "3"
        assert sd["exampleSDID@32473"]["eventSource"] == "Application"
        assert msg["message"] == "An event"

    def test_multiple_elements(self):
        raw = (
            '<165>1 2003-08-24T05:14:15Z host app - - '
            '[e1 k="v1"][e2 k="v2"] msg'
        )
        sd = _parse(raw)["structured_data"]
        assert set(sd.keys()) == {"e1", "e2"}
        assert sd["e1"]["k"] == "v1"
        assert sd["e2"]["k"] == "v2"

    def test_escaped_quote_in_value(self):
        raw = '<34>1 2003-10-11T22:14:15Z h a - - [sd k="say \\"hi\\""] m'
        sd = _parse(raw)["structured_data"]
        assert sd["sd"]["k"] == 'say "hi"'

    def test_escaped_backslash_in_value(self):
        raw = '<34>1 2003-10-11T22:14:15Z h a - - [sd path="C:\\\\Windows"] m'
        sd = _parse(raw)["structured_data"]
        assert sd["sd"]["path"] == "C:\\Windows"

    def test_escaped_bracket_in_value(self):
        raw = r'<34>1 2003-10-11T22:14:15Z h a - - [sd k="a\]b"] m'
        sd = _parse(raw)["structured_data"]
        assert sd["sd"]["k"] == "a]b"

    def test_empty_element(self):
        raw = "<34>1 2003-10-11T22:14:15Z h a - - [origin] msg"
        sd = _parse(raw)["structured_data"]
        assert sd == {"origin": {}}

    def test_value_with_spaces(self):
        raw = '<34>1 2003-10-11T22:14:15Z h a - - [sd msg="hello world"] text'
        sd = _parse(raw)["structured_data"]
        assert sd["sd"]["msg"] == "hello world"

    # Unit-test _parse_sd directly for edge cases
    def test_parse_sd_empty_string(self):
        result, remainder = _parse_sd("")
        assert result == {}
        assert remainder == ""

    def test_parse_sd_nilvalue_not_called_directly(self):
        # '-' is handled in _parse_rfc5424 before _parse_sd is called
        result, remainder = _parse_sd("[a k=\"v\"] rest")
        assert result == {"a": {"k": "v"}}
        assert remainder == " rest"


# ── RFC 3164 ──────────────────────────────────────────────────────────────────

class TestRFC3164:
    def test_basic_message(self):
        raw = "<34>Oct 11 22:14:15 mymachine su: 'su root' failed"
        msg = _parse(raw)
        assert msg["version"] is None
        assert msg["hostname"] == "mymachine"
        assert msg["app_name"] == "su"
        assert msg["proc_id"] is None
        assert "failed" in msg["message"]

    def test_tag_with_pid(self):
        raw = "<38>Oct 11 22:14:15 router sshd[1234]: Accepted key"
        msg = _parse(raw)
        assert msg["app_name"] == "sshd"
        assert msg["proc_id"] == "1234"
        assert msg["message"] == "Accepted key"

    def test_single_digit_day_padded_with_space(self):
        # "Oct  1" (double space) is valid RFC 3164
        raw = "<13>Oct  1 08:00:00 router syslogd: started"
        msg = _parse(raw)
        assert msg["hostname"] == "router"
        assert msg["timestamp"] is not None

    def test_no_tag(self):
        raw = "<13>Feb 25 17:32:18 10.0.0.99 kernel: ath10k: firmware loaded"
        msg = _parse(raw)
        assert msg["hostname"] == "10.0.0.99"
        assert "firmware" in msg["message"]

    def test_hostname_from_message_not_source_ip(self):
        raw = "<13>Oct 11 22:14:15 router1 app: msg"
        msg = _parse(raw, ip="10.9.9.9")
        assert msg["hostname"] == "router1"

    def test_structured_data_empty(self):
        raw = "<13>Oct 11 22:14:15 h p: m"
        assert _parse(raw)["structured_data"] == {}

    def test_msg_id_none(self):
        raw = "<13>Oct 11 22:14:15 h p: m"
        assert _parse(raw)["msg_id"] is None

    def test_timestamp_isoformat(self):
        raw = "<13>Jan 15 10:30:00 host prog: msg"
        msg = _parse(raw)
        assert msg["timestamp"] is not None
        # Must be parseable as ISO 8601
        datetime.fromisoformat(msg["timestamp"])

    def test_facility_severity_3164(self):
        # PRI=165 → facility=20 (local4), severity=5 (notice)  20*8+5=165
        msg = _parse("<165>Oct 11 22:14:15 h p: m")
        _assert_facility_severity(msg, 20, 5)


# ── BOM / encoding edge cases ─────────────────────────────────────────────────

class TestEncoding:
    def test_utf8_bom_stripped_from_message(self):
        raw = "<34>1 2003-10-11T22:14:15Z h a - - - \xef\xbb\xbfhello"
        assert _parse(raw)["message"] == "hello"

    def test_latin1_decoded_with_replacement(self):
        data = b"<13>Oct 11 22:14:15 h p: caf\xe9"
        msg = parse(data, "10.0.0.1", 514, "udp")
        assert "caf" in msg["message"]

    def test_trailing_null_stripped(self):
        data = b"<13>Oct 11 22:14:15 h p: msg\x00\x00"
        msg = parse(data, "10.0.0.1", 514, "udp")
        assert not msg["message"].endswith("\x00")

    def test_trailing_crlf_stripped(self):
        data = b"<13>Oct 11 22:14:15 h p: msg\r\n"
        msg = parse(data, "10.0.0.1", 514, "udp")
        assert not msg["message"].endswith("\r\n")


# ── Malformed / fallback ──────────────────────────────────────────────────────

class TestMalformed:
    def test_no_priority_header(self):
        msg = _parse("This is not syslog")
        assert msg["message"] == "This is not syslog"
        assert msg["hostname"] == "10.0.0.1"
        assert msg["facility"] is None
        assert msg["severity"] is None

    def test_empty_input(self):
        msg = parse(b"", "10.0.0.1", 514, "udp")
        assert msg["message"] == ""

    def test_only_priority(self):
        msg = _parse("<13>")
        assert msg["facility"] is not None
        assert msg["raw"] == "<13>"

    def test_rfc5424_truncated_header(self):
        # Missing required fields → falls back to raw envelope
        msg = _parse("<34>1 2003-10-11T22:14:15Z")
        assert msg["raw"].startswith("<34>")

    def test_priority_only_message_has_source_ip(self):
        msg = _parse("<13> ", ip="5.5.5.5")
        assert msg["source_ip"] == "5.5.5.5"


# ── publisher._sanitise_token ─────────────────────────────────────────────────

class TestSanitiseToken:
    def test_fqdn_preserves_dots(self):
        from ingest.publisher import _sanitise_token
        assert _sanitise_token("router1.example.com") == "router1.example.com"

    def test_ip_preserves_dots(self):
        from ingest.publisher import _sanitise_token
        assert _sanitise_token("192.168.1.1") == "192.168.1.1"

    def test_spaces_replaced(self):
        from ingest.publisher import _sanitise_token
        assert " " not in _sanitise_token("bad hostname")

    def test_empty_returns_unknown(self):
        from ingest.publisher import _sanitise_token
        assert _sanitise_token("") == "unknown"

    def test_none_like_returns_unknown(self):
        from ingest.publisher import _sanitise_token
        assert _sanitise_token(None) == "unknown"  # type: ignore[arg-type]

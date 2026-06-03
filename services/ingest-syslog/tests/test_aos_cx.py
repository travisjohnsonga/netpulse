"""Tests for ingest.aos_cx — HPE AOS-CX syslog normalization."""
from ingest import aos_cx
from ingest.parser import SEVERITIES, parse


def _parse(raw: str, *, ip: str = "10.150.0.21") -> dict:
    return parse(raw.encode(), ip, 514, "udp")


class TestDetect:
    def test_detects_event_format(self):
        assert aos_cx.is_aos_cx_log("Event|4657|LOG_INFO|AMM|-|User admin logged out")
        assert aos_cx.is_aos_cx_log("hpe-restd: Event|4657|LOG_INFO|AMM|-|hi")

    def test_rejects_non_aos_cx(self):
        assert not aos_cx.is_aos_cx_log("%BGP-5-ADJCHANGE: neighbor up")
        assert not aos_cx.is_aos_cx_log("devname=fw type=traffic level=notice")
        assert not aos_cx.is_aos_cx_log("")


class TestParse:
    def test_full_fields(self):
        f = aos_cx.parse_aos_cx_log("hpe-restd: Event|4657|LOG_INFO|AMM|-|User admin logged out")
        assert f["process"] == "hpe-restd"
        assert f["event_id"] == "4657"
        assert f["level"] == "LOG_INFO"
        assert f["module"] == "AMM"
        assert f["message"] == "User admin logged out"

    def test_empty_module_submodule(self):
        f = aos_cx.parse_aos_cx_log("tpmtd: Event|13601|LOG_INFO|||TPM_Sign requested")
        assert f["process"] == "tpmtd"
        assert f["module"] == ""
        assert f["message"] == "TPM_Sign requested"

    def test_no_match_returns_none(self):
        assert aos_cx.parse_aos_cx_log("not an aos-cx log") is None


class TestSeverity:
    def test_level_map(self):
        assert aos_cx.map_aos_cx_severity({"level": "LOG_INFO"}) == 6
        assert aos_cx.map_aos_cx_severity({"level": "LOG_WARN"}) == 4
        assert aos_cx.map_aos_cx_severity({"level": "LOG_ERR"}) == 3
        assert aos_cx.map_aos_cx_severity({"level": "LOG_CRIT"}) == 2
        assert aos_cx.map_aos_cx_severity({"level": "LOG_DEBUG"}) == 7
        assert aos_cx.map_aos_cx_severity({"level": "LOG_BOGUS"}) is None


class TestNormalize:
    def test_message_severity_program(self):
        result = {"message": "hpe-config: Event|6801|LOG_WARN|AMM|-|Copying configs",
                  "severity": 6, "severity_name": "info", "app_name": None}
        aos_cx.normalize(result, SEVERITIES)
        assert result["message"] == "[hpe-config/AMM] Copying configs"
        assert result["severity"] == 4 and result["severity_name"] == "warning"
        assert result["program"] == "HPE-CONFIG"
        assert result["vendor"] == "aruba"
        assert result["extras"]["aos_cx_event_id"] == "6801"
        assert result["extras"]["aos_cx_module"] == "AMM"

    def test_compact_without_module(self):
        result = {"message": "tpmtd: Event|13601|LOG_INFO|||TPM_Sign requested", "app_name": None}
        aos_cx.normalize(result, SEVERITIES)
        assert result["message"] == "[tpmtd] TPM_Sign requested"

    def test_process_from_app_name_when_inband_absent(self):
        # RFC 3164 may split "hpe-restd" into app_name, leaving "Event|…" as message.
        result = {"message": "Event|4657|LOG_INFO|AMM|-|done", "app_name": "hpe-restd"}
        aos_cx.normalize(result, SEVERITIES)
        assert result["message"] == "[hpe-restd/AMM] done"
        assert result["program"] == "HPE-RESTD"

    def test_aruba_central_tagging(self):
        result = {"message":
                  "hpe-restd: Event|4657|LOG_INFO|AMM|-|User admin logged out of REST "
                  "session from device-prod-d2.central.arubanetworks.com", "app_name": None}
        aos_cx.normalize(result, SEVERITIES)
        assert result["extras"]["aruba_central"] == "true"
        assert result["extras"]["aos_cx_source"] == "aruba_central"


class TestEndToEnd:
    def test_full_syslog_line(self):
        # PRI=190 → local7.info; RFC 3164 line from an AOS-CX switch.
        raw = "<190>Jun  3 04:10:00 wco2-idf5-asw-01 hpe-restd: Event|4657|LOG_INFO|AMM|-|User admin logged out"
        msg = _parse(raw)
        assert msg["vendor"] == "aruba"
        assert "[hpe-restd/AMM] User admin logged out" == msg["message"]
        assert msg["raw"] == raw                       # original preserved
        assert msg["extras"]["aos_cx_event_id"] == "4657"

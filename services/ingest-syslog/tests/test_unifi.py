"""Tests for ingest.unifi — Ubiquiti UniFi CEF syslog normalization."""
from ingest import unifi
from ingest.parser import SEVERITIES, parse

# Representative UniFi CEF lines (extension values may contain spaces).
_CONNECT = (
    "CEF:0|Ubiquiti|UniFi Network|9.0.0|400|WiFi Client Connected|3|"
    "UNIFIhost=UDM-Pro UNIFIclientHostname=iPad UNIFIclientIp=10.150.1.77 "
    "UNIFIclientMac=aa:bb:cc:dd:ee:ff UNIFIconnectedToDeviceName=wco2-wld-ap-01 "
    "UNIFIconnectedToDeviceModel=U6-Pro UNIFIwifiName=Vantran Office "
    "UNIFIwifiChannel=104 UNIFIwifiBand=na UNIFIwifiChannelWidth=20 "
    "UNIFIWiFiRssi=-76 UNIFInetworkVlan=1"
)
_DISCONNECT = (
    "CEF:0|Ubiquiti|UniFi Network|9.0.0|401|WiFi Client Disconnected|3|"
    "UNIFIhost=UDM-Pro UNIFIclientHostname=Michael-s-S24-Ultra "
    "UNIFIlastConnectedToDeviceName=IDF8-UpstairsConfRoom "
    "UNIFIlastConnectedToWiFiRssi=-85 UNIFIduration=3660 "
    "UNIFIusageDown=261000 UNIFIusageUp=765000"
)
_CONFIG = (
    "CEF:0|Ubiquiti|UniFi Network|9.0.0|546|Config Modified|5|"
    "UNIFIadmin=UniFi User UNIFIaccessMethod=web "
    "UNIFIsettingsSection=System UNIFIsettingsEntry=unifi_device_management_enabled "
    "src=216.14.43.226"
)
_DEVICE = (
    "CEF:0|Ubiquiti|UniFi Network|9.0.0|500|Device Connected|3|"
    "UNIFIdeviceName=wco2-idf5-asw UNIFIdeviceMac=11:22:33:44:55:66 "
    "UNIFIdeviceModel=USW-Pro UNIFIdeviceVersion=6.5.59"
)


def _parse(raw: str, *, ip: str = "10.150.1.1") -> dict:
    return parse(raw.encode(), ip, 514, "udp")


class TestDetect:
    def test_detects_unifi_cef(self):
        assert unifi.is_unifi_log(_CONNECT)
        assert unifi.is_unifi_log("<14>Jun  9 12:00:00 udm CEF:0|Ubiquiti|UniFi|1|400|x|3|a=b")

    def test_rejects_non_unifi(self):
        assert not unifi.is_unifi_log("CEF:0|SomeVendor|Product|1.0|100|Event|3|foo=bar")
        assert not unifi.is_unifi_log("%BGP-5-ADJCHANGE: neighbor up")
        assert not unifi.is_unifi_log("")


class TestExtensionParsing:
    def test_values_with_spaces(self):
        ext = unifi.parse_cef_extensions(
            "UNIFIadmin=UniFi User UNIFIaccessMethod=web UNIFIsettingsSection=System"
        )
        assert ext["UNIFIadmin"] == "UniFi User"
        assert ext["UNIFIaccessMethod"] == "web"
        assert ext["UNIFIsettingsSection"] == "System"

    def test_empty_extension(self):
        assert unifi.parse_cef_extensions("") == {}

    def test_parse_cef_header(self):
        p = unifi.parse_cef(_CONNECT)
        assert p["vendor"] == "Ubiquiti"
        assert p["event_id"] == "400"
        assert p["name"] == "WiFi Client Connected"
        assert p["severity"] == "3"
        assert p["ext"]["UNIFIclientHostname"] == "iPad"

    def test_parse_cef_no_match(self):
        assert unifi.parse_cef("not a cef line") is None


class TestSeverity:
    def test_cef_severity_map(self):
        assert unifi.map_unifi_severity("3") == 5   # notice
        assert unifi.map_unifi_severity("4") == 4   # warning
        assert unifi.map_unifi_severity("6") == 3   # err
        assert unifi.map_unifi_severity("1") == 6   # info
        assert unifi.map_unifi_severity("bogus") is None


class TestSignalQuality:
    def test_buckets(self):
        assert unifi.classify_signal(-55) == "excellent"
        assert unifi.classify_signal(-65) == "good"
        assert unifi.classify_signal(-76) == "fair"
        assert unifi.classify_signal(-85) == "poor"
        assert unifi.classify_signal(-90) == "very_poor"
        assert unifi.classify_signal(None) == ""


class TestNormalizeWifiConnect:
    def test_connect(self):
        result = {"raw": _CONNECT, "message": "", "severity": 6, "severity_name": "info"}
        unifi.normalize(result, SEVERITIES)
        assert result["vendor"] == "ubiquiti"
        assert result["program"] == "WIRELESS"
        assert result["severity"] == 5 and result["severity_name"] == "notice"
        assert "iPad connected to wco2-wld-ap-01" in result["message"]
        assert "Ch.104 5 GHz 20MHz" in result["message"]
        assert "RSSI -76 dBm fair" in result["message"]
        assert "SSID Vantran Office" in result["message"]
        ex = result["extras"]
        assert ex["unifi_event_type"] == "wifi_client_connected"
        assert ex["unifi_event_id"] == "400"
        assert ex["unifi_client_hostname"] == "iPad"
        assert ex["unifi_ap_name"] == "wco2-wld-ap-01"
        assert ex["unifi_ssid"] == "Vantran Office"
        assert ex["unifi_rssi_dbm"] == -76
        assert ex["unifi_signal_quality"] == "fair"
        assert ex["unifi_band"] == "5 GHz"
        assert ex["unifi_controller_host"] == "UDM-Pro"


class TestNormalizeWifiDisconnect:
    def test_disconnect(self):
        result = {"raw": _DISCONNECT, "message": ""}
        unifi.normalize(result, SEVERITIES)
        assert "Michael-s-S24-Ultra disconnected from IDF8-UpstairsConfRoom" in result["message"]
        assert "duration 3660s" in result["message"]
        ex = result["extras"]
        assert ex["unifi_event_type"] == "wifi_client_disconnected"
        assert ex["unifi_ap_name"] == "IDF8-UpstairsConfRoom"  # from lastConnected* keys
        assert ex["unifi_rssi_dbm"] == -85
        assert ex["unifi_signal_quality"] == "poor"
        assert ex["unifi_duration"] == "3660"
        assert ex["unifi_usage_down"] == "261000"


class TestNormalizeConfig:
    def test_config(self):
        result = {"raw": _CONFIG, "message": ""}
        unifi.normalize(result, SEVERITIES)
        assert result["program"] == "CONFIG"
        assert "Config changed by UniFi User via web" in result["message"]
        assert "System → unifi_device_management_enabled" in result["message"]
        ex = result["extras"]
        assert ex["unifi_event_type"] == "config_modified"
        assert ex["unifi_admin"] == "UniFi User"
        assert ex["unifi_src_ip"] == "216.14.43.226"


class TestNormalizeDevice:
    def test_device_connected(self):
        result = {"raw": _DEVICE, "message": ""}
        unifi.normalize(result, SEVERITIES)
        assert result["program"] == "DEVICE"
        assert result["message"] == "wco2-idf5-asw connected"
        ex = result["extras"]
        assert ex["unifi_event_type"] == "device_connected"
        assert ex["unifi_device_model"] == "USW-Pro"
        assert ex["unifi_device_version"] == "6.5.59"


class TestRobustness:
    def test_missing_fields(self):
        raw = "CEF:0|Ubiquiti|UniFi Network|9.0.0|400|WiFi Client Connected|3|UNIFIclientMac=aa:bb:cc:dd:ee:ff"
        result = {"raw": raw, "message": ""}
        unifi.normalize(result, SEVERITIES)  # must not raise
        assert "aa:bb:cc:dd:ee:ff connected to AP" in result["message"]
        assert result["extras"]["unifi_client_mac"] == "aa:bb:cc:dd:ee:ff"

    def test_non_unifi_cef_ignored(self):
        result = {"raw": "CEF:0|SomeVendor|Product|1.0|100|Event|3|foo=bar",
                  "message": "orig", "vendor": None}
        unifi.normalize(result, SEVERITIES)
        assert result["message"] == "orig"          # untouched
        assert result.get("vendor") is None
        assert "extras" not in result

    def test_unknown_event_id_uses_name(self):
        raw = "CEF:0|Ubiquiti|UniFi Network|9.0.0|999|Something Happened|4|UNIFIhost=UDM"
        result = {"raw": raw, "message": ""}
        unifi.normalize(result, SEVERITIES)
        assert result["message"] == "Something Happened"
        assert result["program"] == "UNIFI"
        assert result["extras"]["unifi_event_type"] == "unifi_event"


class TestEndToEnd:
    def test_full_syslog_line(self):
        # PRI=14 → user.info; RFC 3164 line wrapping a UniFi CEF connect event.
        raw = f"<14>Jun  9 12:00:00 udm-pro {_CONNECT}"
        msg = _parse(raw)
        assert msg["vendor"] == "ubiquiti"
        assert msg["program"] == "WIRELESS"
        assert msg["severity"] == 5  # CEF sev 3 → notice
        assert "iPad connected to wco2-wld-ap-01" in msg["message"]
        assert msg["raw"] == raw  # original preserved
        assert msg["extras"]["unifi_event_id"] == "400"
        assert msg["extras"]["unifi_ssid"] == "Vantran Office"

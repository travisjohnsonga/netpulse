"""
HPE Instant On (1930/1960) platform support.

Fixtures are trimmed from a real 1930 JL684B SNMP walk (firmware 3.3.0.9);
identifiers are generic placeholders. Instant On firmware is Radlan-based with
the RADLAN MIB re-rooted from enterprise 89 to HP's 1.3.6.1.4.1.11.2, and it
exposes neither HOST-RESOURCES-MIB nor ENTITY-SENSOR-MIB.
"""
from apps.compliance.collector import SKIP_CONFIG_PLATFORMS
from apps.devices import enrich
from apps.devices.lldp import guess_platform
from apps.devices.management.commands.run_discovery import (
    _platform_from_descr, _vendor_from_descr,
)
from apps.devices.metrics_influx import FIELD_MAP
from apps.devices.snmp_publish import _device_oids, _walk_oids
from apps.telemetry.snmp_environment import derive_environment

DESCR = ("HPE Networking Instant On Switch 24p Gigabit CL4 PoE 4p SFP+ 370W 1930 "
         "JL684B, InstantOn_1930_3.3.0.0 (9)")
OBJID = "1.3.6.1.4.1.11.2.3.7.11.213"   # HP enterprise 11 — shared with ProCurve

ENV = "1.3.6.1.4.1.11.2.53.15.1"
CHASSIS, PSU, FAN1, FAN2 = "67109120", "67109505", "67109633", "67109634"
WALK = {
    # entPhysicalClass / entPhysicalName
    f"1.3.6.1.2.1.47.1.1.1.1.5.{CHASSIS}": "3",
    f"1.3.6.1.2.1.47.1.1.1.1.5.{PSU}": "6",
    f"1.3.6.1.2.1.47.1.1.1.1.5.{FAN1}": "7",
    f"1.3.6.1.2.1.47.1.1.1.1.5.{FAN2}": "7",
    f"1.3.6.1.2.1.47.1.1.1.1.7.{CHASSIS}": "Instant On 1930 24G 4SFP+ 370W Switch JL684B",
    f"1.3.6.1.2.1.47.1.1.1.1.7.{PSU}": "Main PowerSupply",
    f"1.3.6.1.2.1.47.1.1.1.1.7.{FAN1}": "Fan1",
    f"1.3.6.1.2.1.47.1.1.1.1.7.{FAN2}": "Fan2",
    # pethMainPseTable: budget in TRUE watts (370 W model), 7 W drawn
    "1.3.6.1.2.1.105.1.3.1.1.2.1": "370",
    "1.3.6.1.2.1.105.1.3.1.1.3.1": "1",
    "1.3.6.1.2.1.105.1.3.1.1.4.1": "7",
    # rlPhdUnitEnvParamTable, unit 1
    f"{ENV}.1.1": "1",
    f"{ENV}.2.1": "1",    # main PS normal
    f"{ENV}.3.1": "5",    # redundant PS notPresent
    f"{ENV}.4.1": "1",    # fan1 normal
    f"{ENV}.5.1": "1",    # fan2 normal
    f"{ENV}.6.1": "5", f"{ENV}.7.1": "5", f"{ENV}.8.1": "5", f"{ENV}.9.1": "5",
    f"{ENV}.10.1": "65",  # °C
    f"{ENV}.11.1": "1",
    f"{ENV}.12.1": "103",
    f"{ENV}.13.1": "108",
}


class TestDetection:
    def test_platform_from_descr(self):
        assert _platform_from_descr(DESCR) == "instant_on"
        # Older firmware brands as "Aruba Instant On" — must not fall to "aruba".
        assert _platform_from_descr("Aruba Instant On 1930 8G 2SFP Switch JL680A, "
                                    "InstantOn_1930_2.4.0.0 (12)") == "instant_on"

    def test_aos_cx_detection_unchanged(self):
        assert _platform_from_descr("HPE ANW R9Y04A 6100 48G CL4 4SFP+ Sw PL.10.16.1030") == "aos_cx"

    def test_vendor(self):
        assert _vendor_from_descr(DESCR) == "aruba"

    def test_lldp_hint(self):
        assert guess_platform(DESCR) == "instant_on"


class TestIdentity:
    def test_parse_descr(self):
        assert enrich._parse_instant_on_descr(DESCR) == {
            "model": "1930 JL684B", "os_version": "3.3.0.9"}
        assert enrich._parse_instant_on_descr("Cisco IOS Software, Version 15.2") == {}

    def test_parse_snmp_prefers_entphysical_model(self):
        res = {
            "1.3.6.1.2.1.1.1.0": DESCR,
            "1.3.6.1.2.1.1.2.0": OBJID,
            "1.3.6.1.2.1.47.1.1.1.1.13.1": "Instant On 1930 24G 4SFP+ 370W Switch JL684B",
            "1.3.6.1.2.1.47.1.1.1.1.11.1": "SN0000000001",
        }
        updates: dict = {}
        enrich._parse_snmp(res, updates)
        assert updates == {
            "os_version": "3.3.0.9",
            "platform": "instant_on",
            "vendor": "aruba",
            "model": "Instant On 1930 24G 4SFP+ 370W Switch JL684B",
            "serial_number": "SN0000000001",
        }

    def test_parse_snmp_descr_model_fallback(self):
        updates: dict = {}
        enrich._parse_snmp({"1.3.6.1.2.1.1.1.0": DESCR}, updates)
        assert updates["model"] == "1930 JL684B"

    def test_aos_cx_model_parser_ignores_instant_on(self):
        # Regression: the "HPE …" AOS-CX parser used to store the whole sysDescr.
        assert enrich._model_from_aos_cx_descr(DESCR) == ""


class TestPolling:
    def test_cpu_oid_polled_and_mapped(self):
        oids = _device_oids("instant_on")
        assert "1.3.6.1.4.1.11.2.1.8.0" in oids
        assert "1.3.6.1.2.1.25.3.3.1.2.1" not in oids   # no HOST-RESOURCES-MIB
        assert FIELD_MAP["1_3_6_1_4_1_11_2_1_8_0"] == "cpu_pct"

    def test_walks_env_table(self):
        assert ENV in _walk_oids("instant_on")

    def test_config_collection_skipped(self):
        assert "instant_on" in SKIP_CONFIG_PLATFORMS


class TestEnvironment:
    def test_temperature_and_unit_status(self):
        env = derive_environment({}, WALK)
        assert env["scalars"]["temp_max_c"] == 65.0
        assert env["temperature"] == [
            {"name": "System", "index": "1", "celsius": 65.0, "status_ok": True}]
        assert env["scalars"]["fan_count"] == 2
        assert env["scalars"]["psu_count"] == 1
        assert [(f["name"], f["status_ok"]) for f in env["fans"]] == [("Fan1", True), ("Fan2", True)]
        assert [(p["name"], p["status_ok"]) for p in env["psus"]] == [("Main PowerSupply", True)]

    def test_failed_fan_reported(self):
        walk = {**WALK, f"{ENV}.5.1": "3"}   # fan2 critical
        fans = derive_environment({}, walk)["fans"]
        assert [f["status_ok"] for f in fans] == [True, False]

    def test_poe_budget_in_true_watts(self):
        poe = derive_environment({}, WALK)["poe"]
        assert poe["budget_watts"] == 370.0
        assert poe["used_watts"] == 7.0
        assert poe["used_pct"] == 1.9

    def test_aos_cx_poe_divisor_unchanged(self):
        walk = {"1.3.6.1.2.1.105.1.3.1.1.2.1": "740", "1.3.6.1.2.1.105.1.3.1.1.4.1": "56"}
        assert derive_environment({}, walk)["poe"]["budget_watts"] == 370.0

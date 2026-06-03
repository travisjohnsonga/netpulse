"""Environment metric derivation (apps.telemetry.snmp_environment).

Data mirrors the real HPE AOS-CX 6100 (wco2-idf5-asw-01, 10.150.0.21).
"""
from apps.telemetry import snmp_environment as env


def _aos_cx_walk():
    """Walk results captured from the real 6100 (10.150.0.21)."""
    w = {
        # hrProcessorLoad — two cores at vendor indexes (NOT .1).
        f"{env.HR_PROCESSOR_LOAD}.196608": "23",
        f"{env.HR_PROCESSOR_LOAD}.196609": "22",
        # ENTITY-SENSOR: one celsius sensor (107001).
        f"{env.ENT_SENSOR_TYPE}.107001": "8",
        f"{env.ENT_SENSOR_SCALE}.107001": "8",       # milli
        f"{env.ENT_SENSOR_PRECISION}.107001": "0",
        f"{env.ENT_SENSOR_VALUE}.107001": "28875",
        f"{env.ENT_SENSOR_STATUS}.107001": "1",      # ok
        f"{env.ENT_PHYSICAL_NAME}.107001": "Temp Sensor",
        # ENTITY-SENSOR: PSU power sensor (107201, type 6 watts), reads 0 W ok.
        f"{env.ENT_SENSOR_TYPE}.107201": "6",
        f"{env.ENT_SENSOR_SCALE}.107201": "9",       # units
        f"{env.ENT_SENSOR_PRECISION}.107201": "0",
        f"{env.ENT_SENSOR_VALUE}.107201": "0",
        f"{env.ENT_SENSOR_STATUS}.107201": "1",      # ok
        f"{env.ENT_PHYSICAL_NAME}.107201": "Power sensor for power supply 1/1",
        # entPhysicalClass — 1 PSU (6) + 4 fans (7).
        f"{env.ENT_PHYSICAL_CLASS}.113001": "6",
        f"{env.ENT_PHYSICAL_NAME}.113001": "1/1",
    }
    # Four fans (entPhysicalClass 7) each with an RPM sensor (type 10) that the
    # 6100 reports as -1 (unavailable) but oper-status ok(1).
    for n in range(1, 5):
        sidx = 107100 + n
        fidx = 115000 + n
        w[f"{env.ENT_SENSOR_TYPE}.{sidx}"] = "10"
        w[f"{env.ENT_SENSOR_SCALE}.{sidx}"] = "9"
        w[f"{env.ENT_SENSOR_PRECISION}.{sidx}"] = "0"
        w[f"{env.ENT_SENSOR_VALUE}.{sidx}"] = "-1"   # unavailable
        w[f"{env.ENT_SENSOR_STATUS}.{sidx}"] = "1"   # ok
        w[f"{env.ENT_PHYSICAL_NAME}.{sidx}"] = f"RPM sensor for fan System-1/1/{n}"
        w[f"{env.ENT_PHYSICAL_CLASS}.{fidx}"] = "7"
        w[f"{env.ENT_PHYSICAL_NAME}.{fidx}"] = f"System-1/1/{n}"
    # POWER-ETHERNET-MIB pethMainPseTable walk (one PSE group), raw OIDs as the
    # device returns them: budget 740 (half-watts → 370 W), status on(1), 56 W.
    w[f"{env.PETH_PSE_POWER}.1"] = "740"             # 105.1.3.1.1.2.1
    w[f"{env.PETH_PSE_OPER_STATUS}.1"] = "1"         # 105.1.3.1.1.3.1 — on
    w[f"{env.PETH_PSE_CONSUMPTION}.1"] = "56"        # 105.1.3.1.1.4.1
    return w


def _aos_cx_get():
    return {
        f"{env.HR_STORAGE_SIZE}.1": "3499600",
        f"{env.HR_STORAGE_USED}.1": "1006196",
        f"{env.HR_STORAGE_ALLOC}.1": "1024",
    }


class TestDeriveEnvironment:
    def test_cpu_is_averaged_across_cores(self):
        result = env.derive_environment({}, _aos_cx_walk())
        assert result["scalars"]["cpu_pct"] == 22.5   # (23 + 22) / 2

    def test_memory_percent_and_bytes(self):
        result = env.derive_environment(_aos_cx_get(), {})
        s = result["scalars"]
        assert s["memory_used_pct"] == 28.8          # 1006196 / 3499600
        assert s["memory_total_bytes"] == 3499600 * 1024
        assert s["memory_used_bytes"] == 1006196 * 1024

    def test_temperature_scaling(self):
        result = env.derive_environment({}, _aos_cx_walk())
        temps = result["temperature"]
        assert len(temps) == 1                       # only the celsius sensor
        t = temps[0]
        assert t["name"] == "Temp Sensor"
        assert abs(t["celsius"] - 28.875) < 0.01      # 28875 × 10^-3 (milli)
        assert t["status_ok"] is True
        assert result["scalars"]["temp_max_c"] == t["celsius"]

    def test_fan_and_psu_inventory(self):
        result = env.derive_environment({}, _aos_cx_walk())
        assert result["scalars"]["fan_count"] == 4
        assert result["scalars"]["psu_count"] == 1
        assert {f["name"] for f in result["fans"]} == {f"System-1/1/{n}" for n in range(1, 5)}
        assert result["psus"][0]["name"] == "1/1"

    def test_fan_detail_rpm_unavailable_but_status_ok(self):
        fans = env.derive_environment({}, _aos_cx_walk())["fans"]
        assert len(fans) == 4
        for f in fans:
            assert f["rpm"] is None          # -1 from device → unavailable
            assert f["status_ok"] is True    # per-fan oper-status IS exposed

    def test_psu_detail_watts_and_status(self):
        psu = env.derive_environment({}, _aos_cx_walk())["psus"][0]
        assert psu["name"] == "1/1"
        assert psu["watts"] == 0.0           # reads 0 W (valid), not unavailable
        assert psu["status_ok"] is True

    def test_poe_budget_and_usage(self):
        poe = env.derive_environment({}, _aos_cx_walk())["poe"]
        assert poe["budget_watts"] == 370.0  # 740 raw / 2 (half-watt units)
        assert poe["used_watts"] == 56.0
        assert poe["used_pct"] == 15.1       # 56 / 370 * 100
        assert poe["status"] == "on"

    def test_poe_status_codes(self):
        w = dict(_aos_cx_walk())
        w[f"{env.PETH_PSE_OPER_STATUS}.1"] = "3"
        assert env.derive_environment({}, w)["poe"]["status"] == "faulty"

    def test_no_poe_section_when_unsupported(self):
        # No pethMainPseTable in the walk → no poe key at all.
        walk = {f"{env.ENT_PHYSICAL_CLASS}.1": "7", f"{env.ENT_PHYSICAL_NAME}.1": "Fan 1"}
        assert "poe" not in env.derive_environment({}, walk)

    def test_unit_without_sensor_has_unknown_status(self):
        # entPhysicalClass fan present but no RPM sensor → status unknown (None).
        walk = {f"{env.ENT_PHYSICAL_CLASS}.1": "7", f"{env.ENT_PHYSICAL_NAME}.1": "Fan 1"}
        fan = env.derive_environment({}, walk)["fans"][0]
        assert fan["rpm"] is None and fan["status_ok"] is None

    def test_units_scale_with_precision(self):
        # scale=units(9), precision=1, value=288 → 28.8 °C (the common form).
        walk = {
            f"{env.ENT_SENSOR_TYPE}.1": "8", f"{env.ENT_SENSOR_SCALE}.1": "9",
            f"{env.ENT_SENSOR_PRECISION}.1": "1", f"{env.ENT_SENSOR_VALUE}.1": "288",
        }
        t = env.derive_environment({}, walk)["temperature"][0]
        assert t["celsius"] == 28.8

    def test_failed_sensor_flagged(self):
        walk = {
            f"{env.ENT_SENSOR_TYPE}.1": "8", f"{env.ENT_SENSOR_SCALE}.1": "9",
            f"{env.ENT_SENSOR_VALUE}.1": "40", f"{env.ENT_SENSOR_STATUS}.1": "3",  # nonoperational
        }
        t = env.derive_environment({}, walk)["temperature"][0]
        assert t["status_ok"] is False

    def test_empty_inputs_are_safe(self):
        result = env.derive_environment({}, {})
        assert result == {"scalars": {}, "temperature": [], "fans": [], "psus": []}

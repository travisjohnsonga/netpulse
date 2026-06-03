"""Environment metric derivation (apps.telemetry.snmp_environment).

Data mirrors the real HPE AOS-CX 6100 (wco2-idf5-asw-01, 10.150.0.21).
"""
from apps.telemetry import snmp_environment as env


def _aos_cx_walk():
    """Walk results captured from the real 6100."""
    return {
        # hrProcessorLoad — two cores at vendor indexes (NOT .1).
        f"{env.HR_PROCESSOR_LOAD}.196608": "23",
        f"{env.HR_PROCESSOR_LOAD}.196609": "22",
        # ENTITY-SENSOR: one celsius sensor (107001) + rpm fans reading -1.
        f"{env.ENT_SENSOR_TYPE}.107001": "8",
        f"{env.ENT_SENSOR_SCALE}.107001": "8",       # milli
        f"{env.ENT_SENSOR_PRECISION}.107001": "0",
        f"{env.ENT_SENSOR_VALUE}.107001": "28875",
        f"{env.ENT_SENSOR_STATUS}.107001": "1",      # ok
        f"{env.ENT_PHYSICAL_NAME}.107001": "Temp Sensor",
        f"{env.ENT_SENSOR_TYPE}.107101": "10",       # rpm (fan) — not celsius
        f"{env.ENT_SENSOR_VALUE}.107101": "-1",
        # entPhysicalClass — 1 PSU (6) + 4 fans (7).
        f"{env.ENT_PHYSICAL_CLASS}.113001": "6",
        f"{env.ENT_PHYSICAL_NAME}.113001": "1/1",
        f"{env.ENT_PHYSICAL_CLASS}.115001": "7",
        f"{env.ENT_PHYSICAL_NAME}.115001": "System-1/1/1",
        f"{env.ENT_PHYSICAL_CLASS}.115002": "7",
        f"{env.ENT_PHYSICAL_CLASS}.115003": "7",
        f"{env.ENT_PHYSICAL_CLASS}.115004": "7",
    }


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
        assert {f["name"] for f in result["fans"]} >= {"System-1/1/1"}
        assert result["psus"][0]["name"] == "1/1"

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

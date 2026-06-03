"""
Derive normalized environment metrics from raw SNMP GET + walk results.

The ingest-snmp poller publishes:
  - ``metrics``: GET results  {oid: {value, name, ...}}
  - ``walk``:    table walks   {oid: value}             (env tables)

This module turns those into:
  - scalar telemetry fields (cpu_pct, memory_used_pct, memory_*_bytes,
    temp_max_c, fan_count, psu_count) merged into the ``telemetry`` measurement
  - per-sensor temperature readings written to the ``device_environment``
    measurement

Pure functions, no IO — unit-testable against captured device data. AOS-CX 6100
exposes CPU via HOST-RESOURCES hrProcessorLoad (at vendor indexes, not .1),
memory via hrStorage (index 1 = "Physical memory"), and temperature via the
standard ENTITY-SENSOR-MIB; fan/PSU presence via ENTITY-MIB entPhysicalClass.
"""
from __future__ import annotations

# HOST-RESOURCES-MIB column bases (no trailing index).
HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"
HR_STORAGE_ALLOC = "1.3.6.1.2.1.25.2.3.1.4"

# ENTITY-SENSOR-MIB (RFC 3433) column bases.
ENT_SENSOR_TYPE = "1.3.6.1.2.1.99.1.1.1.1"
ENT_SENSOR_SCALE = "1.3.6.1.2.1.99.1.1.1.2"
ENT_SENSOR_PRECISION = "1.3.6.1.2.1.99.1.1.1.3"
ENT_SENSOR_VALUE = "1.3.6.1.2.1.99.1.1.1.4"
ENT_SENSOR_STATUS = "1.3.6.1.2.1.99.1.1.1.5"
# ENTITY-MIB entPhysicalTable column bases.
ENT_PHYSICAL_CLASS = "1.3.6.1.2.1.47.1.1.1.1.5"
ENT_PHYSICAL_NAME = "1.3.6.1.2.1.47.1.1.1.1.7"

# All table bases an env-capable device should be told to walk.
WALK_BASES = [
    HR_PROCESSOR_LOAD,
    ENT_SENSOR_TYPE, ENT_SENSOR_SCALE, ENT_SENSOR_PRECISION,
    ENT_SENSOR_VALUE, ENT_SENSOR_STATUS,
    ENT_PHYSICAL_CLASS, ENT_PHYSICAL_NAME,
]

SENSOR_TYPE_CELSIUS = 8   # EntitySensorDataType.celsius(8)
SENSOR_STATUS_OK = 1      # EntitySensorStatus.ok(1)
PHYS_CLASS_PSU = 6        # entPhysicalClass.powerSupply(6)
PHYS_CLASS_FAN = 7        # entPhysicalClass.fan(7)

# EntitySensorDataScale enum → power-of-ten exponent (RFC 3433).
_SCALE_EXP = {
    1: -24, 2: -21, 3: -18, 4: -15, 5: -12, 6: -9, 7: -6, 8: -3,
    9: 0, 10: 3, 11: 6, 12: 9, 13: 12, 14: 15, 15: 18, 16: 21, 17: 24,
}


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def _by_index(walk: dict, base: str) -> dict:
    """Return {index_suffix: value} for every OID under ``base.`` in ``walk``."""
    prefix = base + "."
    return {oid[len(prefix):]: val for oid, val in walk.items() if oid.startswith(prefix)}


def _cpu_pct(walk: dict):
    """Average hrProcessorLoad across all CPUs (AOS-CX reports per-core, no .1)."""
    loads = [_to_float(v) for v in _by_index(walk, HR_PROCESSOR_LOAD).values()]
    loads = [v for v in loads if v is not None and v >= 0]
    if not loads:
        return None
    return round(sum(loads) / len(loads), 1)


def _memory(get_values: dict) -> dict:
    """memory_used_pct + bytes from hrStorage index 1 (Physical memory)."""
    size = _to_float(get_values.get(HR_STORAGE_SIZE + ".1"))
    used = _to_float(get_values.get(HR_STORAGE_USED + ".1"))
    alloc = _to_float(get_values.get(HR_STORAGE_ALLOC + ".1")) or 1.0
    if not size or used is None:
        return {}
    return {
        "memory_used_pct": round(used / size * 100, 1),
        "memory_total_bytes": size * alloc,
        "memory_used_bytes": used * alloc,
    }


def _temperature_sensors(walk: dict) -> list[dict]:
    """ENTITY-SENSOR-MIB celsius sensors → [{name, index, celsius, status_ok}]."""
    types = _by_index(walk, ENT_SENSOR_TYPE)
    values = _by_index(walk, ENT_SENSOR_VALUE)
    scales = _by_index(walk, ENT_SENSOR_SCALE)
    precisions = _by_index(walk, ENT_SENSOR_PRECISION)
    statuses = _by_index(walk, ENT_SENSOR_STATUS)
    names = _by_index(walk, ENT_PHYSICAL_NAME)

    out = []
    for idx, stype in types.items():
        if _to_int(stype) != SENSOR_TYPE_CELSIUS:
            continue
        raw = _to_float(values.get(idx))
        if raw is None:
            continue
        scale_exp = _SCALE_EXP.get(_to_int(scales.get(idx)), 0)
        precision = _to_int(precisions.get(idx)) or 0
        celsius = raw * (10 ** scale_exp) * (10 ** (-precision))
        status_ok = _to_int(statuses.get(idx)) == SENSOR_STATUS_OK if idx in statuses else True
        out.append({
            "name": names.get(idx) or f"sensor-{idx}",
            "index": idx,
            "celsius": round(celsius, 2),
            "status_ok": status_ok,
        })
    return out


def _inventory(walk: dict) -> tuple[list[dict], list[dict]]:
    """Fan + PSU presence from entPhysicalClass (status not exposed on the 6100)."""
    classes = _by_index(walk, ENT_PHYSICAL_CLASS)
    names = _by_index(walk, ENT_PHYSICAL_NAME)
    fans, psus = [], []
    for idx, cls in classes.items():
        c = _to_int(cls)
        if c == PHYS_CLASS_FAN:
            fans.append({"name": names.get(idx) or f"fan-{idx}", "index": idx})
        elif c == PHYS_CLASS_PSU:
            psus.append({"name": names.get(idx) or f"psu-{idx}", "index": idx})
    return fans, psus


def derive_environment(get_values: dict, walk: dict) -> dict:
    """
    Derive environment metrics from raw SNMP results.

    get_values: {oid: value} from the GET poll (hrStorage memory lives here)
    walk:       {oid: value} from the table walks (CPU/sensors/inventory)

    Returns:
      {
        "scalars": {cpu_pct?, memory_used_pct?, memory_total_bytes?,
                    memory_used_bytes?, temp_max_c?, fan_count, psu_count},
        "temperature": [{name, index, celsius, status_ok}, ...],
        "fans":  [{name, index}, ...],
        "psus":  [{name, index}, ...],
      }
    """
    get_values = get_values or {}
    walk = walk or {}

    scalars: dict = {}
    cpu = _cpu_pct(walk)
    if cpu is not None:
        scalars["cpu_pct"] = cpu
    scalars.update(_memory(get_values))

    temps = _temperature_sensors(walk)
    if temps:
        scalars["temp_max_c"] = max(t["celsius"] for t in temps)

    fans, psus = _inventory(walk)
    if fans:
        scalars["fan_count"] = len(fans)
    if psus:
        scalars["psu_count"] = len(psus)

    return {"scalars": scalars, "temperature": temps, "fans": fans, "psus": psus}

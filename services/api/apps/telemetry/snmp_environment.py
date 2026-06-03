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

# POWER-ETHERNET-MIB pethMainPseTable column bases. NOTE the entry index .1:
# columns are pethMainPseEntry(.1).<col>, e.g. pethMainPsePower = 105.1.3.1.1.2
# (lab-verified on AOS-CX 6100: ...1.2.1=740, ...1.3.1=1 (on), ...1.4.1=56).
# Collected by WALK, not GET: the device answers a walk of the table but returns
# "Wrong SNMP PDU digest" on a scalar GET of these instances. Raw OIDs matched
# directly — POWER-ETHERNET-MIB is intentionally NOT in our MIB collection, so
# the poller returns them numerically (no name resolution).
PETH_PSE_POWER = "1.3.6.1.2.1.105.1.3.1.1.2"        # pethMainPsePower (budget)
PETH_PSE_OPER_STATUS = "1.3.6.1.2.1.105.1.3.1.1.3"  # 1=on 2=off 3=faulty
PETH_PSE_CONSUMPTION = "1.3.6.1.2.1.105.1.3.1.1.4"  # pethMainPseConsumptionPower (W used)
PETH_MAIN_PSE_ENTRY = "1.3.6.1.2.1.105.1.3.1"       # table — one walk grabs all columns

# AOS-CX reports pethMainPsePower at twice the rated budget (740 raw for the
# 6100 48G CL4's 370 W PoE budget) — i.e. half-watt units — while
# pethMainPseConsumptionPower reads true watts. Scale the budget down so
# used/budget % is meaningful (56 W / 370 W ≈ 15%). Revisit per-platform if a
# device is found that reports the budget in true watts.
_POE_BUDGET_DIVISOR = 2

# All table bases an env-capable device should be told to walk.
WALK_BASES = [
    HR_PROCESSOR_LOAD,
    ENT_SENSOR_TYPE, ENT_SENSOR_SCALE, ENT_SENSOR_PRECISION,
    ENT_SENSOR_VALUE, ENT_SENSOR_STATUS,
    ENT_PHYSICAL_CLASS, ENT_PHYSICAL_NAME,
    PETH_MAIN_PSE_ENTRY,
]

SENSOR_TYPE_WATTS = 6     # EntitySensorDataType.watts(6)
SENSOR_TYPE_CELSIUS = 8   # EntitySensorDataType.celsius(8)
SENSOR_TYPE_RPM = 10      # EntitySensorDataType.rpm(10)
SENSOR_STATUS_OK = 1      # EntitySensorStatus.ok(1)
PHYS_CLASS_PSU = 6        # entPhysicalClass.powerSupply(6)
PHYS_CLASS_FAN = 7        # entPhysicalClass.fan(7)

# Prefixes the ENTITY-SENSOR name carries that we strip to label a unit when no
# entPhysicalTable entity is present to borrow a cleaner name from. (Observed on
# AOS-CX 6100: "RPM sensor for fan System-1/1/1", "Power sensor for power
# supply 1/1".)
_SENSOR_NAME_PREFIXES = (
    "RPM sensor for fan ",
    "Power sensor for power supply ",
    "Power sensor for ",
    "Fan sensor for ",
)

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


def _strip_sensor_prefix(name: str) -> str:
    for pfx in _SENSOR_NAME_PREFIXES:
        if name.startswith(pfx):
            return name[len(pfx):]
    return name


def _sensor_map(walk: dict, want_type: int) -> dict:
    """
    ENTITY-SENSOR-MIB entries of ``want_type`` →
    {idx: {"value": float|None, "status_ok": bool|None, "name": str}}.

    ``value`` is scaled by EntitySensorDataScale + precision; a raw -1 (the
    device's "unavailable", e.g. AOS-CX fan RPM) becomes None. ``status_ok``
    comes from entPhySensorOperStatus (1=ok) — the per-unit status the 6100 does
    expose, even though entPhysicalTable carries none.
    """
    types = _by_index(walk, ENT_SENSOR_TYPE)
    values = _by_index(walk, ENT_SENSOR_VALUE)
    scales = _by_index(walk, ENT_SENSOR_SCALE)
    precisions = _by_index(walk, ENT_SENSOR_PRECISION)
    statuses = _by_index(walk, ENT_SENSOR_STATUS)
    names = _by_index(walk, ENT_PHYSICAL_NAME)

    out: dict = {}
    for idx, stype in types.items():
        if _to_int(stype) != want_type:
            continue
        raw = _to_float(values.get(idx))
        if raw is None or raw < 0:
            value = None
        else:
            scale_exp = _SCALE_EXP.get(_to_int(scales.get(idx)), 0)
            precision = _to_int(precisions.get(idx)) or 0
            value = round(raw * (10 ** scale_exp) * (10 ** (-precision)), 2)
        status_ok = (_to_int(statuses.get(idx)) == SENSOR_STATUS_OK) if idx in statuses else None
        out[idx] = {"value": value, "status_ok": status_ok, "name": names.get(idx) or ""}
    return out


def _units(walk: dict, phys_class: int, sensor_type: int, reading_key: str) -> list[dict]:
    """
    Build a per-unit list (fans or PSUs), preferring entPhysicalTable entities
    for naming/presence and overlaying the matching ENTITY-SENSOR reading +
    status. Falls back to the sensors directly when a device exposes sensors but
    no entPhysical entities.

    Returns [{name, index, <reading_key>: float|None, status_ok: bool|None}].
    """
    classes = _by_index(walk, ENT_PHYSICAL_CLASS)
    names = _by_index(walk, ENT_PHYSICAL_NAME)
    sensors = _sensor_map(walk, sensor_type)

    entities = [(idx, names.get(idx) or "") for idx, c in classes.items()
                if _to_int(c) == phys_class]

    units: list[dict] = []
    used: set = set()
    if entities:
        for idx, name in sorted(entities, key=lambda x: x[0]):
            match = None
            for sidx, s in sensors.items():
                # The sensor name embeds the unit name, e.g.
                # "RPM sensor for fan System-1/1/1" contains "System-1/1/1".
                if sidx not in used and name and name in s["name"]:
                    match = (sidx, s)
                    break
            if match:
                used.add(match[0])
                units.append({"name": name or f"unit-{idx}", "index": idx,
                              reading_key: match[1]["value"], "status_ok": match[1]["status_ok"]})
            else:
                units.append({"name": name or f"unit-{idx}", "index": idx,
                              reading_key: None, "status_ok": None})
    else:
        for sidx, s in sorted(sensors.items()):
            units.append({"name": _strip_sensor_prefix(s["name"]) or f"unit-{sidx}",
                          "index": sidx, reading_key: s["value"], "status_ok": s["status_ok"]})
    return units


def _inventory(walk: dict) -> tuple[list[dict], list[dict]]:
    """Per-fan (with RPM) and per-PSU (with watts) detail + per-unit status."""
    fans = _units(walk, PHYS_CLASS_FAN, SENSOR_TYPE_RPM, "rpm")
    psus = _units(walk, PHYS_CLASS_PSU, SENSOR_TYPE_WATTS, "watts")
    return fans, psus


def _poe(walk: dict) -> dict:
    """
    POWER-ETHERNET-MIB pethMainPseTable → PoE budget/usage from the table walk,
    summed across PSE groups. {} when the device exposes no PoE table.
    status: on/off/faulty.
    """
    power = _by_index(walk, PETH_PSE_POWER)
    consumed = _by_index(walk, PETH_PSE_CONSUMPTION)
    statuses = _by_index(walk, PETH_PSE_OPER_STATUS)
    if not power and not consumed:
        return {}

    budget = sum(v for v in (_to_float(x) for x in power.values()) if v is not None)
    budget = budget / _POE_BUDGET_DIVISOR
    used = sum(v for v in (_to_float(x) for x in consumed.values()) if v is not None)
    codes = [_to_int(v) for v in statuses.values() if _to_int(v) is not None]
    if 3 in codes:
        status = "faulty"
    elif codes and all(c == 2 for c in codes):
        status = "off"
    else:
        status = "on"

    return {
        "budget_watts": round(budget, 1),
        "used_watts": round(used, 1),
        "used_pct": round(used / budget * 100, 1) if budget > 0 else None,
        "status": status,
    }


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
        "fans":  [{name, index, rpm:   float|None, status_ok: bool|None}, ...],
        "psus":  [{name, index, watts: float|None, status_ok: bool|None}, ...],
        "poe":   {budget_watts, used_watts, used_pct, status} (omitted if none),
      }
    rpm/watts are None when the device reports the reading as unavailable
    (AOS-CX fan RPM reads -1); status_ok is None when no per-unit sensor exists.
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

    result = {"scalars": scalars, "temperature": temps, "fans": fans, "psus": psus}
    poe = _poe(walk)
    if poe:
        result["poe"] = poe
    return result

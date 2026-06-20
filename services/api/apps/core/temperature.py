"""Temperature conversion + display helpers.

Temperatures are stored and returned by the API in **Celsius**; the per-user
``UserPreferences.temperature_unit`` only controls how they're displayed. These
helpers convert/format for any server-side rendering (e.g. PDF reports); the SPA
does the same conversion client-side at render time.
"""
from __future__ import annotations


def celsius_to_fahrenheit(c: float) -> float:
    return (c * 9 / 5) + 32


def fahrenheit_to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9


def format_temperature(celsius: float | None, unit: str = "C", *, decimals: int = 1) -> str:
    """Format a Celsius value for display in the requested unit.

    ``unit`` is "C" or "F" (case-insensitive); anything else falls back to "C".
    Returns an em dash for ``None``.
    """
    if celsius is None:
        return "—"
    if (unit or "C").upper() == "F":
        return f"{celsius_to_fahrenheit(celsius):.{decimals}f}°F"
    return f"{celsius:.{decimals}f}°C"

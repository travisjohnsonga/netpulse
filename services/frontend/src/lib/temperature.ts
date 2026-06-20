import { useUnitsStore, type TempUnit } from '../store/unitsStore'

export function cToF(c: number): number {
  return (c * 9) / 5 + 32
}

/** Convert a Celsius value to the active unit (no formatting). */
export function toUnit(celsius: number, unit: TempUnit): number {
  return unit === 'F' ? cToF(celsius) : celsius
}

/** Format a Celsius value for display in the given unit; em dash for null. */
export function formatTemp(
  celsius: number | null | undefined,
  unit: TempUnit,
  decimals = 1,
): string {
  if (celsius == null || Number.isNaN(celsius)) return '—'
  if (unit === 'F') return `${cToF(celsius).toFixed(decimals)}°F`
  return `${celsius.toFixed(decimals)}°C`
}

/**
 * Hook giving the active temperature unit plus convenience helpers. All inputs
 * are Celsius (the API's storage unit); output respects the user's preference.
 */
export function useTemperature() {
  const unit = useUnitsStore((s) => s.unit)
  return {
    unit,
    suffix: unit === 'F' ? '°F' : '°C',
    /** Format a Celsius value as a string with unit (e.g. "75.0°C"). */
    format: (c: number | null | undefined, decimals = 1) => formatTemp(c, unit, decimals),
    /** Convert a Celsius value to the active unit's numeric value (for charts). */
    convert: (c: number) => toUnit(c, unit),
  }
}

// Human-readable byte counts (1.2 MB, 456 KB, …) for flow volumes.
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  // Bytes: whole numbers. Larger units: 2 sig figs below 10, else 1 decimal / whole.
  const formatted = i === 0 ? String(Math.round(v)) : v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : String(Math.round(v))
  return `${formatted} ${units[i]}`
}

// Compact "time ago" — 23s / 4m / 2h / 20d. Shared by the Devices and Servers
// "Last Change" column so both read identically (down = how long down, up = how
// long since last contact).
export function compactAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

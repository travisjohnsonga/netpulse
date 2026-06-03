// Helpers for the IP/MAC lookup feature (dedicated page + header quick-search).

export type QueryKind = 'ip' | 'mac' | 'unknown'

const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/
// MAC in any common separator style (colon, dash, dot) or bare 12 hex digits.
const MAC = /^(?:[0-9a-f]{2}([:-]?)){5}[0-9a-f]{2}$|^[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}$|^[0-9a-f]{12}$/i

// Classify a raw lookup query so the UI can label what it's searching for.
// The backend accepts either; this is purely for display + light validation.
export function detectQueryKind(q: string): QueryKind {
  const v = q.trim()
  if (IPV4.test(v)) return 'ip'
  if (MAC.test(v)) return 'mac'
  return 'unknown'
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

// Newest collected_at across all ARP/MAC matches, or null when there are none.
export function latestCollected(times: (string | null | undefined)[]): string | null {
  let best: number | null = null
  for (const t of times) {
    if (!t) continue
    const ms = new Date(t).getTime()
    if (!Number.isNaN(ms) && (best === null || ms > best)) best = ms
  }
  return best === null ? null : new Date(best).toISOString()
}

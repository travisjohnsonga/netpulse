// Syslog severity → Tailwind badge classes (canonical long names from the API).
export const SEVERITY_ORDER = [
  'emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'debug',
] as const

export function severityBadge(sev: string): string {
  switch ((sev || '').toLowerCase()) {
    case 'emergency':
    case 'alert':
    case 'critical':
      return 'bg-red-100 text-red-700'
    case 'error':
      return 'bg-orange-100 text-orange-700'
    case 'warning':
      return 'bg-yellow-100 text-yellow-700'
    case 'notice':
    case 'info':
      return 'bg-blue-100 text-blue-700'
    default:
      return 'bg-gray-100 text-gray-500'
  }
}

export const TIME_RANGES: { id: string; label: string; seconds: number | null }[] = [
  { id: '15m', label: 'Last 15 min', seconds: 900 },
  { id: '1h', label: 'Last 1 hr', seconds: 3600 },
  { id: '4h', label: 'Last 4 hr', seconds: 14400 },
  { id: '12h', label: 'Last 12 hr', seconds: 43200 },
  { id: '24h', label: 'Last 24 hr', seconds: 86400 },
  { id: '7d', label: 'Last 7 days', seconds: 604800 },
  { id: 'all', label: 'All time', seconds: null },
]

export function rangeFrom(rangeId: string): string | undefined {
  const r = TIME_RANGES.find((x) => x.id === rangeId)
  if (!r || r.seconds == null) return undefined
  return new Date(Date.now() - r.seconds * 1000).toISOString()
}

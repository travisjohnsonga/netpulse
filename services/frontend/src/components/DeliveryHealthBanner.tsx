import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchDeliveryHealth, type DeliveryHealth } from '../api/client'

// Always-present surface for the §3 banner tier: when notification delivery is
// degraded (a channel persistently failing), show a persistent banner linking to
// the delivery log. Polls /delivery-health/; clears automatically on recovery.
const POLL_MS = 60_000

export default function DeliveryHealthBanner() {
  const [health, setHealth] = useState<DeliveryHealth | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchDeliveryHealth().then((h) => { if (!cancelled) setHealth(h) }).catch(() => {})
    load()
    const t = setInterval(load, POLL_MS)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  if (!health || health.healthy || health.channels_failing < 1) return null
  const n = health.channels_failing
  return (
    <Link
      to="/notifications"
      className="flex items-center gap-2 px-4 py-1.5 bg-red-50 dark:bg-red-900/30 border-b border-red-200 dark:border-red-800 text-sm text-red-800 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-800/40 transition-colors"
    >
      <span aria-hidden>⚠️</span>
      <span>
        <strong>Alert delivery degraded</strong> — {n} channel{n === 1 ? '' : 's'} failing.
        Push notifications (email/Teams) may not be reaching anyone.
      </span>
      <span className="ml-auto px-2 py-0.5 text-xs font-medium rounded border border-red-300 dark:border-red-700">
        View delivery log →
      </span>
    </Link>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import { checkInfraHealth, fetchCollectors, fetchDeliveryHealth, type InfraHealth, type InfraServiceHealth, type Collector, type DeliveryHealth } from '../../api/client'

// Human label per infrastructure service key, in display order.
const SERVICES: { key: keyof InfraHealth['services']; label: string }[] = [
  { key: 'postgres', label: 'PostgreSQL' },
  { key: 'valkey', label: 'Valkey' },
  { key: 'nats', label: 'NATS' },
  { key: 'influxdb', label: 'InfluxDB' },
  { key: 'opensearch', label: 'OpenSearch' },
  { key: 'openbao', label: 'OpenBao' },
]

const REFRESH_MS = 30_000

function agoStr(iso?: string): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

function StatusBadge({ svc }: { svc?: InfraServiceHealth }) {
  if (!svc) {
    return <span className="text-gray-400 dark:text-gray-500">…</span>
  }
  return svc.ok ? (
    <span className="inline-flex items-center gap-1.5 text-green-600 dark:text-green-400 font-medium">
      <span className="w-2 h-2 rounded-full bg-green-500" /> OK
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-red-600 dark:text-red-400 font-medium">
      <span className="w-2 h-2 rounded-full bg-red-500" /> Down
    </span>
  )
}

export default function PlatformStatus() {
  const [health, setHealth] = useState<InfraHealth | null>(null)
  const [delivery, setDelivery] = useState<DeliveryHealth | null>(null)
  const [localCollector, setLocalCollector] = useState<Collector | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // Re-render so the "X ago" column ticks between fetches.
  const [, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      checkInfraHealth()
        .then((d) => { if (!cancelled) { setHealth(d); setError(null); setLoading(false) } })
        .catch(() => { if (!cancelled) { setError('Could not reach the API.'); setLoading(false) } })
      fetchCollectors()
        .then((cs) => { if (!cancelled) setLocalCollector(cs.find((c) => c.collector_type === 'local') ?? null) })
        .catch(() => {})
      fetchDeliveryHealth()
        .then((d) => { if (!cancelled) setDelivery(d) })
        .catch(() => {})
    }
    load()
    const refresh = setInterval(load, REFRESH_MS)
    const ticker = setInterval(() => setTick((t) => t + 1), 1000)
    return () => { cancelled = true; clearInterval(refresh); clearInterval(ticker) }
  }, [])

  return (
    <div>
      <SectionHeader
        title="Platform Status"
        description="Internal spane service health"
      />

      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 mb-4">
          {error}
        </div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Service</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Response</th>
                <th className="px-5 py-3 font-medium">Checked</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {SERVICES.map(({ key, label }) => {
                const svc = health?.services?.[key]
                return (
                  <tr key={key} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">{label}</td>
                    <td className="px-5 py-3"><StatusBadge svc={svc} /></td>
                    <td className={clsx('px-5 py-3 font-mono',
                      svc?.response_ms != null ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500')}>
                      {svc?.response_ms != null ? `${svc.response_ms}ms` : '—'}
                    </td>
                    <td className="px-5 py-3 text-gray-500 dark:text-gray-400">
                      {loading ? '…' : agoStr(health?.checked_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Local collector — this server's own telemetry collector */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 px-5 py-4 mt-4 flex items-center gap-3 text-sm">
        <span className="font-medium text-gray-700 dark:text-gray-200">Local Collector:</span>
        {localCollector ? (
          <span className={clsx('inline-flex items-center gap-1.5 font-medium',
            localCollector.is_healthy ? 'text-green-600 dark:text-green-400' : 'text-gray-500 dark:text-gray-400')}>
            <span className={clsx('w-2 h-2 rounded-full', localCollector.is_healthy ? 'bg-green-500' : 'bg-gray-400')} />
            {localCollector.is_healthy ? 'Healthy' : 'No heartbeat'}
            <span className="text-gray-400 dark:text-gray-500 font-normal">
              · last seen {agoStr(localCollector.last_seen_at ?? undefined)}
            </span>
          </span>
        ) : (
          <span className="text-gray-400 dark:text-gray-500">not registered yet</span>
        )}
        <a href="/settings/collectors" className="ml-auto text-blue-600 hover:text-blue-800 text-xs">Manage →</a>
      </div>

      {/* Notification delivery — per-channel delivery health (PR #152). "Service up"
          ≠ "delivery working": a healthy engine can still fail every send. */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 px-5 py-4 mt-4 text-sm">
        <div className="flex items-center gap-2 mb-2">
          <span className="font-medium text-gray-700 dark:text-gray-200">Notification Delivery</span>
          {delivery && (
            <span className={clsx('inline-flex items-center gap-1.5 font-medium',
              delivery.healthy ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
              <span className={clsx('w-2 h-2 rounded-full', delivery.healthy ? 'bg-green-500' : 'bg-red-500')} />
              {delivery.healthy ? 'Healthy' : `${delivery.channels_failing} channel(s) failing`}
            </span>
          )}
          <Link to="/notifications" className="ml-auto text-blue-600 hover:text-blue-800 text-xs">Delivery log →</Link>
        </div>
        {!delivery || delivery.channels.length === 0 ? (
          <p className="text-gray-400 dark:text-gray-500 text-xs">No notifications sent in the last hour.</p>
        ) : (
          <div className="space-y-1.5">
            {delivery.channels.map((c) => (
              <div key={`${c.channel_id ?? c.channel_type}`} className="flex items-center gap-2 text-xs">
                <span className={clsx('w-2 h-2 rounded-full', c.healthy ? 'bg-green-500' : 'bg-red-500')} />
                <span className="font-medium text-gray-700 dark:text-gray-200">{c.channel_name || c.channel_type}</span>
                <span className="text-gray-400 dark:text-gray-500">
                  {c.failed} failed / {c.sent} sent · last ok {agoStr(c.last_success ?? undefined)}
                  {!c.healthy && c.last_failure ? ` · last fail ${agoStr(c.last_failure ?? undefined)}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-gray-400 dark:text-gray-500 mt-3">
        Auto-refreshes every 30s
        {health?.version && health.version !== 'unknown' ? ` · spane ${health.version}` : ''}
      </p>
    </div>
  )
}

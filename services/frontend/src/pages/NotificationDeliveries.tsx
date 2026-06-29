import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchNotificationDeliveries, fetchDeliveryHealth, fetchAlertChannels,
  type NotificationDelivery, type DeliveryHealth, type AlertChannel,
} from '../api/client'

const selCls = 'px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'

function agoStr(iso?: string | null): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export default function NotificationDeliveries() {
  const [rows, setRows] = useState<NotificationDelivery[]>([])
  const [health, setHealth] = useState<DeliveryHealth | null>(null)
  const [channels, setChannels] = useState<AlertChannel[]>([])
  const [status, setStatus] = useState('')
  const [channel, setChannel] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchAlertChannels().then(setChannels).catch(() => {})
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () => {
      setLoading(true)
      const params: { status?: string; channel?: string } = {}
      if (status) params.status = status
      if (channel) params.channel = channel
      fetchNotificationDeliveries(params)
        .then((d) => { if (!cancelled) { setRows(d); setError(null) } })
        .catch(() => { if (!cancelled) setError('Could not load delivery log.') })
        .finally(() => { if (!cancelled) setLoading(false) })
      fetchDeliveryHealth().then((h) => { if (!cancelled) setHealth(h) }).catch(() => {})
    }
    load()
    const t = setInterval(load, 30_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [status, channel])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Notification Delivery</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Every alert-notification attempt (sent/failed) — the source of truth for "did it deliver?".
          </p>
        </div>
        {health && (
          <span className={clsx('inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1 rounded-full',
            health.healthy
              ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400'
              : 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400')}>
            <span className={clsx('w-2 h-2 rounded-full', health.healthy ? 'bg-green-500' : 'bg-red-500')} />
            {health.healthy ? 'Delivery healthy' : `${health.channels_failing} channel(s) failing`}
          </span>
        )}
      </div>

      {/* Per-channel health summary */}
      {health && health.channels.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {health.channels.map((c) => (
            <span key={`${c.channel_id ?? c.channel_type}`}
              className={clsx('inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border',
                c.healthy
                  ? 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'
                  : 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400')}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', c.healthy ? 'bg-green-500' : 'bg-red-500')} />
              {c.channel_name || c.channel_type}
              <span className="text-gray-400 dark:text-gray-500">
                · {c.failed} failed / {c.sent} sent · last ok {agoStr(c.last_success)}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <select className={selCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
        </select>
        <select className={selCls} value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option>
          {channels.map((c) => <option key={c.id} value={String(c.id)}>{c.name} ({c.channel_type})</option>)}
        </select>
      </div>

      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Time</th>
                <th className="px-5 py-3 font-medium">Alert</th>
                <th className="px-5 py-3 font-medium">Channel</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Attempts</th>
                <th className="px-5 py-3 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {loading && rows.length === 0 && (
                <tr><td colSpan={6} className="px-5 py-8 text-center text-gray-400">Loading…</td></tr>
              )}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={6} className="px-5 py-8 text-center text-gray-400">No deliveries in range.</td></tr>
              )}
              {rows.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 align-top">
                  <td className="px-5 py-2.5 text-gray-500 dark:text-gray-400 whitespace-nowrap font-mono text-xs">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="px-5 py-2.5 text-gray-800 dark:text-gray-100">
                    {r.event_title || `event ${r.event}`}
                    <span className="block text-xs text-gray-400 dark:text-gray-500">{r.transition}</span>
                  </td>
                  <td className="px-5 py-2.5 text-gray-700 dark:text-gray-300">
                    {r.channel_name || '—'}
                    <span className="block text-xs text-gray-400 dark:text-gray-500">{r.channel_type}</span>
                  </td>
                  <td className="px-5 py-2.5">
                    <span className={clsx('inline-flex items-center gap-1.5 font-medium',
                      r.status === 'sent' ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
                      <span className={clsx('w-2 h-2 rounded-full', r.status === 'sent' ? 'bg-green-500' : 'bg-red-500')} />
                      {r.status === 'sent' ? 'Sent' : 'Failed'}
                    </span>
                  </td>
                  <td className="px-5 py-2.5 text-gray-500 dark:text-gray-400 font-mono">{r.attempts}</td>
                  <td className="px-5 py-2.5 text-gray-500 dark:text-gray-400 max-w-md">
                    <span className="block truncate" title={r.detail}>{r.detail || '—'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-gray-400 dark:text-gray-500">
        Auto-refreshes every 30s. Health window: last {health?.window_minutes ?? 60} minutes.
      </p>
    </div>
  )
}

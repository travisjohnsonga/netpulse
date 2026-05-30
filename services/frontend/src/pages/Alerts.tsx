import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import { fetchAlerts, acknowledgeAlert, type Alert } from '../api/client'

type Severity = 'all' | 'critical' | 'high' | 'medium' | 'low'

const SEVERITY_TABS: { key: Severity; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
]

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 border border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800',
  high: 'bg-orange-100 text-orange-700 border border-orange-200 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800',
  medium: 'bg-yellow-100 text-yellow-700 border border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400 dark:border-yellow-800',
  low: 'bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
  info: 'bg-green-100 text-green-700 border border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
}

const STATE_BADGE: Record<string, string> = {
  firing: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
  acknowledged: 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  resolved: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400',
}

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

export default function Alerts() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [severityFilter, setSeverityFilter] = useState<Severity>('all')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchAlerts()
      .then((data) => {
        if (!cancelled) {
          setAlerts(data)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not load alerts. Check that the API is running.')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [])

  // Interface alerts carry their real severity on the event; fall back to the
  // rule severity for ordinary alerts.
  const sevOf = (a: Alert) => a.effective_severity ?? a.severity

  const filtered = severityFilter === 'all'
    ? alerts
    : alerts.filter((a) => sevOf(a) === severityFilter)

  const counts: Record<Severity, number> = {
    all: alerts.length,
    critical: alerts.filter((a) => sevOf(a) === 'critical').length,
    high: alerts.filter((a) => sevOf(a) === 'high').length,
    medium: alerts.filter((a) => sevOf(a) === 'medium').length,
    low: alerts.filter((a) => sevOf(a) === 'low').length,
  }

  const handleAcknowledge = (id: number) => {
    setAlerts((prev) =>
      prev.map((a) => (a.id === id ? { ...a, state: 'acknowledged' as const } : a)),
    )
    acknowledgeAlert(id).catch(() => {
      // Roll back on failure.
      setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, state: 'firing' as const } : a)))
    })
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Alerts</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          {counts.all > 0
            ? `${counts.all} alert${counts.all !== 1 ? 's' : ''} — ${counts.critical} critical`
            : 'No active alerts'}
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-yellow-50 border border-yellow-200 dark:bg-yellow-900/30 dark:border-yellow-800 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-300">
          {error}
        </div>
      )}

      {/* Severity filter tabs */}
      <div className="flex gap-1 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-1 w-fit">
        {SEVERITY_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSeverityFilter(tab.key)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
              severityFilter === tab.key
                ? 'bg-gray-900 text-white dark:bg-blue-600'
                : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700',
            )}
          >
            {tab.label}
            {counts[tab.key] > 0 && (
              <span
                className={clsx(
                  'text-xs px-1.5 py-0.5 rounded-full',
                  severityFilter === tab.key
                    ? 'bg-white/20 text-white'
                    : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
                )}
              >
                {counts[tab.key]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Alerts table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title={severityFilter === 'all' ? 'No active alerts' : `No ${severityFilter} alerts`}
            description={
              severityFilter === 'all'
                ? 'Your network is healthy. Alerts will appear here when triggered by the alert engine.'
                : `No ${severityFilter} severity alerts right now. Try viewing all alerts.`
            }
            action={
              severityFilter !== 'all'
                ? { label: 'View All Alerts', onClick: () => setSeverityFilter('all') }
                : undefined
            }
            icon="✅"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Alert</th>
                  <th className="px-5 py-3 font-medium">Device</th>
                  <th className="px-5 py-3 font-medium">Details</th>
                  <th className="px-5 py-3 font-medium">Fired At</th>
                  <th className="px-5 py-3 font-medium">State</th>
                  <th className="px-5 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {filtered.map((alert) => {
                  const sev = sevOf(alert)
                  const isDown = alert.is_interface_alert && alert.transition === 'down'
                  const isRecovery = alert.is_interface_alert && alert.transition === 'up'
                  return (
                  <tr key={alert.id} className={clsx(
                    'hover:bg-gray-50 dark:hover:bg-gray-700/50',
                    isDown && 'border-l-2 border-l-red-500',
                    isRecovery && 'border-l-2 border-l-green-500',
                  )}>
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          SEVERITY_BADGE[sev] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
                        )}
                      >
                        {sev}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {alert.is_interface_alert ? (
                        <div className="flex items-center gap-1.5">
                          <span>{isDown ? '🔴' : isRecovery ? '🟢' : '🔌'}</span>
                          <div>
                            <span className={clsx('font-semibold',
                              isDown ? 'text-red-600 dark:text-red-400' : isRecovery ? 'text-green-600 dark:text-green-400' : 'text-gray-800 dark:text-gray-100')}>
                              {alert.interface}
                            </span>
                            <span className="block text-xs text-gray-400 dark:text-gray-500">
                              {isDown ? 'Interface down' : isRecovery ? 'Interface recovered' : alert.rule_name}
                            </span>
                          </div>
                        </div>
                      ) : (
                        <span className="font-medium text-gray-800 dark:text-gray-100">{alert.title || alert.rule_name}</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-300 text-xs font-mono">
                      {alert.device_id ? (
                        <Link to={`/devices/${alert.device_id}?tab=telemetry`} className="text-blue-600 dark:text-blue-400 hover:underline">
                          {alert.device || `device ${alert.device_id}`}
                        </Link>
                      ) : (alert.device || '—')}
                    </td>
                    <td className="px-5 py-3 text-gray-500 dark:text-gray-400 max-w-xs">
                      <span className="block truncate">{alert.message}</span>
                      {isRecovery && alert.downtime_seconds != null && (
                        <span className="text-xs text-green-600 dark:text-green-400">Down for {formatDuration(alert.downtime_seconds)}</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-gray-500 dark:text-gray-400 text-xs whitespace-nowrap">
                      {new Date(alert.fired_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          STATE_BADGE[alert.state] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
                        )}
                      >
                        {alert.state}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {alert.state === 'firing' && (
                        <button
                          onClick={() => handleAcknowledge(alert.id)}
                          className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-800 font-medium"
                        >
                          Acknowledge
                        </button>
                      )}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

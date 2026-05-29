import { useEffect, useState } from 'react'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import { fetchAlerts, type Alert } from '../api/client'

type Severity = 'all' | 'critical' | 'high' | 'medium' | 'low'

const SEVERITY_TABS: { key: Severity; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
]

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 border border-red-200',
  high: 'bg-orange-100 text-orange-700 border border-orange-200',
  medium: 'bg-yellow-100 text-yellow-700 border border-yellow-200',
  low: 'bg-blue-100 text-blue-700 border border-blue-200',
}

const STATE_BADGE: Record<string, string> = {
  firing: 'bg-red-50 text-red-600',
  acknowledged: 'bg-yellow-50 text-yellow-700',
  resolved: 'bg-green-50 text-green-700',
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

  const filtered = severityFilter === 'all'
    ? alerts
    : alerts.filter((a) => a.severity === severityFilter)

  const counts: Record<Severity, number> = {
    all: alerts.length,
    critical: alerts.filter((a) => a.severity === 'critical').length,
    high: alerts.filter((a) => a.severity === 'high').length,
    medium: alerts.filter((a) => a.severity === 'medium').length,
    low: alerts.filter((a) => a.severity === 'low').length,
  }

  const handleAcknowledge = (id: number) => {
    setAlerts((prev) =>
      prev.map((a) => (a.id === id ? { ...a, state: 'acknowledged' as const } : a)),
    )
    // TODO: call PATCH /api/alerts/events/{id}/ when API is ready
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Alerts</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          {counts.all > 0
            ? `${counts.all} alert${counts.all !== 1 ? 's' : ''} — ${counts.critical} critical`
            : 'No active alerts'}
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">
          {error}
        </div>
      )}

      {/* Severity filter tabs */}
      <div className="flex gap-1 bg-white rounded-lg shadow-sm border border-gray-200 p-1 w-fit">
        {SEVERITY_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSeverityFilter(tab.key)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
              severityFilter === tab.key
                ? 'bg-gray-900 text-white'
                : 'text-gray-600 hover:bg-gray-100',
            )}
          >
            {tab.label}
            {counts[tab.key] > 0 && (
              <span
                className={clsx(
                  'text-xs px-1.5 py-0.5 rounded-full',
                  severityFilter === tab.key
                    ? 'bg-white/20 text-white'
                    : 'bg-gray-100 text-gray-600',
                )}
              >
                {counts[tab.key]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Alerts table */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
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
                <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Rule</th>
                  <th className="px-5 py-3 font-medium">Device</th>
                  <th className="px-5 py-3 font-medium">Message</th>
                  <th className="px-5 py-3 font-medium">Fired At</th>
                  <th className="px-5 py-3 font-medium">State</th>
                  <th className="px-5 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((alert) => (
                  <tr key={alert.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          SEVERITY_BADGE[alert.severity] ?? 'bg-gray-100 text-gray-600',
                        )}
                      >
                        {alert.severity}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-medium text-gray-800">{alert.rule_name}</td>
                    <td className="px-5 py-3 text-gray-600 text-xs font-mono">{alert.device}</td>
                    <td className="px-5 py-3 text-gray-500 max-w-xs truncate">{alert.message}</td>
                    <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">
                      {new Date(alert.fired_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          STATE_BADGE[alert.state] ?? 'bg-gray-100 text-gray-600',
                        )}
                      >
                        {alert.state}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {alert.state === 'firing' && (
                        <button
                          onClick={() => handleAcknowledge(alert.id)}
                          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                        >
                          Acknowledge
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import AlertDetails from '../components/AlertDetails'
import { Fragment } from 'react'
import { fetchAlerts, acknowledgeAlert, resolveAlertEvent, fetchAlertNotifications, fetchDevices, type Alert, type AlertNotificationRecord } from '../api/client'
import { useSite } from '../store/siteStore'

type View = 'false' | 'true' | 'all'
const VIEW_TABS: { key: View; label: string }[] = [
  { key: 'false', label: 'Active' },
  { key: 'true', label: 'Resolved' },
  { key: 'all', label: 'All' },
]

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
  const [view, setView] = useState<View>('false')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [timeline, setTimeline] = useState<Record<number, AlertNotificationRecord[]>>({})
  // Device ids belonging to the active site (null = no site filter). Alerts carry
  // a device_id, so we scope client-side to devices at the selected site.
  const { selectedSite } = useSite()
  const [siteDeviceIds, setSiteDeviceIds] = useState<Set<number> | null>(null)

  useEffect(() => {
    if (!selectedSite) { setSiteDeviceIds(null); return }
    let cancelled = false
    fetchDevices({ site: selectedSite, page_size: '1000' })
      .then((d) => { if (!cancelled) setSiteDeviceIds(new Set(d.results.map((x) => x.id))) })
      .catch(() => { if (!cancelled) setSiteDeviceIds(new Set()) })
    return () => { cancelled = true }
  }, [selectedSite])

  const reload = (v: View) => {
    setView(v)
    setLoading(true)
    fetchAlerts(v).then(setAlerts).catch(() => setError('Could not load alerts.')).finally(() => setLoading(false))
  }
  const handleResolve = (id: number) => {
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, state: 'resolved' as const } : a)))
    resolveAlertEvent(id).then(() => { if (view === 'false') reload('false') })
      .catch(() => setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, state: 'firing' as const } : a))))
  }

  const toggleTimeline = (id: number) => {
    setExpandedId((cur) => (cur === id ? null : id))
    if (timeline[id] === undefined) {
      fetchAlertNotifications(id)
        .then((rows) => setTimeline((t) => ({ ...t, [id]: rows })))
        .catch(() => setTimeline((t) => ({ ...t, [id]: [] })))
    }
  }

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

  // Scope to the active site first (alerts whose device is at the site), then
  // apply the severity tab filter on top.
  const siteAlerts = siteDeviceIds
    ? alerts.filter((a) => a.device_id != null && siteDeviceIds.has(a.device_id))
    : alerts

  const filtered = severityFilter === 'all'
    ? siteAlerts
    : siteAlerts.filter((a) => sevOf(a) === severityFilter)

  const counts: Record<Severity, number> = {
    all: siteAlerts.length,
    critical: siteAlerts.filter((a) => sevOf(a) === 'critical').length,
    high: siteAlerts.filter((a) => sevOf(a) === 'high').length,
    medium: siteAlerts.filter((a) => sevOf(a) === 'medium').length,
    low: siteAlerts.filter((a) => sevOf(a) === 'low').length,
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

      {/* Active / Resolved / All toggle */}
      <div className="flex gap-1 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-1 w-fit">
        {VIEW_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => reload(tab.key)}
            className={clsx('px-3 py-1.5 text-sm font-medium rounded-md transition-colors',
              view === tab.key ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50')}
          >
            {tab.label}
          </button>
        ))}
      </div>

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
                  const open = expandedId === alert.id
                  return (
                  <Fragment key={alert.id}>
                  <tr onClick={() => toggleTimeline(alert.id)} className={clsx(
                    'hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer',
                    alert.state === 'resolved' && 'opacity-60',
                    isDown && 'border-l-2 border-l-red-500',
                    isRecovery && 'border-l-2 border-l-green-500',
                  )}>
                    <td className="px-5 py-3">
                      <span className="text-gray-400 text-xs mr-1.5">{open ? '▼' : '▶'}</span>
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
                      {alert.state === 'resolved' ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                          title={alert.resolved_at ? `Resolved ${new Date(alert.resolved_at).toLocaleString()}` : 'Resolved'}>
                          ✅ {alert.resolved_by === 'user' ? 'Resolved by user' : alert.resolved_by === 'auto' ? 'Resolved automatically' : 'Resolved'}
                        </span>
                      ) : (
                        <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          STATE_BADGE[alert.state] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300')}>
                          {alert.state}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 whitespace-nowrap">
                      {alert.state === 'firing' && (
                        <>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleAcknowledge(alert.id) }}
                            className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-800 font-medium mr-3"
                          >
                            Acknowledge
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleResolve(alert.id) }}
                            className="text-xs text-green-600 dark:text-green-400 hover:text-green-800 font-medium"
                          >
                            Resolve
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr className="bg-gray-50/60 dark:bg-gray-900/40">
                      <td colSpan={7} className="px-5 py-3 space-y-3">
                        {/* Header: device / severity / fired / state */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                          <div><span className="text-gray-400">Device:</span> <span className="text-gray-700 dark:text-gray-200">{alert.device || '—'}</span></div>
                          <div><span className="text-gray-400">Severity:</span> <span className="text-gray-700 dark:text-gray-200 capitalize">{sev}</span></div>
                          <div><span className="text-gray-400">Fired:</span> <span className="text-gray-700 dark:text-gray-200">{new Date(alert.fired_at).toLocaleString()}</span></div>
                          <div><span className="text-gray-400">State:</span> <span className="text-gray-700 dark:text-gray-200 capitalize">{alert.state}</span></div>
                        </div>
                        {/* Type-aware details (diff viewer / summary / text) */}
                        {(alert.details || alert.message) && (
                          <div className="border-t border-gray-200 dark:border-gray-700 pt-2">
                            <AlertDetails alert={alert} />
                          </div>
                        )}
                        <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-2 border-t border-gray-200 dark:border-gray-700 pt-2">Notification timeline</p>
                        <ul className="space-y-1 text-xs text-gray-600 dark:text-gray-300">
                          <li><span className="text-gray-400">{new Date(alert.fired_at).toLocaleString()}</span> — Alert created ({sev})</li>
                          {timeline[alert.id] === undefined && <li className="text-gray-400">Loading…</li>}
                          {timeline[alert.id]?.length === 0 && <li className="text-gray-400">No notifications recorded for this alert.</li>}
                          {timeline[alert.id]?.map((n) => (
                            <li key={n.id}>
                              <span className="text-gray-400">{new Date(n.sent_at || n.created_at).toLocaleString()}</span>
                              {' — '}
                              {n.channel} to {n.username || 'team'}{' '}
                              <span className={clsx('font-medium',
                                n.status === 'sent' ? 'text-green-600 dark:text-green-400'
                                  : n.status === 'failed' ? 'text-red-600 dark:text-red-400'
                                  : n.status === 'cancelled' ? 'text-gray-400' : 'text-yellow-600 dark:text-yellow-400')}>
                                {n.status === 'sent' ? '✅' : n.status === 'failed' ? '❌' : ''} {n.status}
                              </span>
                              {n.error && <span className="text-red-500"> · {n.error}</span>}
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                  </Fragment>
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

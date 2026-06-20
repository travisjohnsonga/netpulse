import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import AlertDetails from '../components/AlertDetails'
import { Fragment } from 'react'
import { fetchAlertsByState, fetchAlertSummary, acknowledgeAlert, resolveAlertEvent, bulkAcknowledgeAlerts, bulkResolveAlerts, fetchAlertNotifications, fetchDevices, type Alert, type AlertStateCounts, type AlertNotificationRecord } from '../api/client'
import { useSite } from '../store/siteStore'

type View = 'all' | 'firing' | 'acknowledged' | 'resolved'
const VIEW_TABS: { key: View; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'firing', label: 'Firing' },
  { key: 'acknowledged', label: 'Acknowledged' },
  { key: 'resolved', label: 'Resolved' },
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
  const [view, setView] = useState<View>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [timeline, setTimeline] = useState<Record<number, AlertNotificationRecord[]>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [stateCounts, setStateCounts] = useState<AlertStateCounts | null>(null)
  const [showResolveConfirm, setShowResolveConfirm] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
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

  const flash = (msg: string) => { setToast(msg); window.setTimeout(() => setToast(null), 3000) }

  const refreshSummary = () => { fetchAlertSummary().then(setStateCounts).catch(() => {}) }

  const load = (v: View) => {
    setLoading(true)
    fetchAlertsByState(v)
      .then((data) => { setAlerts(data); setSelected(new Set()) })
      .catch(() => setError('Could not load alerts.'))
      .finally(() => setLoading(false))
    refreshSummary()
  }
  const reload = (v: View) => { setView(v); load(v) }

  const handleResolve = (id: number) => {
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, state: 'resolved' as const } : a)))
    resolveAlertEvent(id).then(() => { refreshSummary(); if (view !== 'all' && view !== 'resolved') load(view) })
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
    fetchAlertsByState('all')
      .then((data) => { if (!cancelled) { setAlerts(data); setLoading(false) } })
      .catch(() => { if (!cancelled) { setError('Could not load alerts. Check that the API is running.'); setLoading(false) } })
    fetchAlertSummary().then((c) => { if (!cancelled) setStateCounts(c) }).catch(() => {})
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

  // Derived display state: the model only stores firing/resolved; an "acked"
  // firing event carries is_acknowledged. Acknowledged events show the
  // Acknowledged badge and hide the Acknowledge action (Resolve stays).
  const displayState = (a: Alert): 'firing' | 'acknowledged' | 'resolved' =>
    a.state === 'resolved' ? 'resolved' : a.is_acknowledged ? 'acknowledged' : 'firing'

  const handleAcknowledge = (id: number) => {
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, is_acknowledged: true } : a)))
    acknowledgeAlert(id).then(refreshSummary).catch(() => {
      setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, is_acknowledged: false } : a)))
    })
  }

  // ── Selection ──────────────────────────────────────────────────────────────
  const toggleSelect = (id: number) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const visibleIds = filtered.map((a) => a.id)
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id))
  const someSelected = selected.size > 0 && !allSelected
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visibleIds))
  const clearSelection = () => setSelected(new Set())

  // ── Bulk actions ─────────────────────────────────────────────────────────────
  const selectedIds = () => Array.from(selected)
  const doBulkAcknowledge = async () => {
    const ids = selectedIds()
    if (!ids.length) return
    setBulkBusy(true)
    try {
      const r = await bulkAcknowledgeAlerts(ids)
      flash(`${r.updated} alert${r.updated !== 1 ? 's' : ''} acknowledged`)
      clearSelection(); load(view)
    } catch { flash('Bulk acknowledge failed') } finally { setBulkBusy(false) }
  }
  const doBulkResolve = async () => {
    const ids = selectedIds()
    if (!ids.length) return
    setBulkBusy(true)
    try {
      const r = await bulkResolveAlerts(ids)
      flash(`${r.updated} alert${r.updated !== 1 ? 's' : ''} resolved`)
      clearSelection(); setShowResolveConfirm(false); load(view)
    } catch { flash('Bulk resolve failed') } finally { setBulkBusy(false) }
  }
  const requestBulkResolve = () => {
    if (selected.size >= 5) setShowResolveConfirm(true)
    else doBulkResolve()
  }

  // ── Keyboard shortcuts: a=ack, r=resolve, Esc=clear, Ctrl/Cmd+A=select all ──
  const kbd = useRef({ doBulkAcknowledge, requestBulkResolve, clearSelection, toggleAll, hasSel: selected.size > 0 })
  kbd.current = { doBulkAcknowledge, requestBulkResolve, clearSelection, toggleAll, hasSel: selected.size > 0 }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); kbd.current.toggleAll(); return }
      if (e.key === 'Escape') { kbd.current.clearSelection(); return }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (!kbd.current.hasSel) return
      if (e.key.toLowerCase() === 'a') { e.preventDefault(); kbd.current.doBulkAcknowledge() }
      else if (e.key.toLowerCase() === 'r') { e.preventDefault(); kbd.current.requestBulkResolve() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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

      {/* State filter tabs: All / Firing / Acknowledged / Resolved (+counts) */}
      <div className="flex gap-1 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-1 w-fit">
        {VIEW_TABS.map((tab) => {
          const count = stateCounts ? stateCounts[tab.key] : undefined
          return (
            <button
              key={tab.key}
              onClick={() => reload(tab.key)}
              className={clsx('flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors',
                view === tab.key ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50')}
            >
              {tab.label}
              {count !== undefined && (
                <span className={clsx('text-xs px-1.5 py-0.5 rounded-full',
                  view === tab.key ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300')}>
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Bulk action toolbar — appears when 1+ alerts are selected */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-2.5 text-sm">
          <span className="font-medium text-blue-800 dark:text-blue-300">
            ✓ {selected.size} alert{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={doBulkAcknowledge}
              disabled={bulkBusy}
              className="px-3 py-1.5 rounded-md bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 font-medium"
            >
              Acknowledge <kbd className="ml-1 text-xs text-gray-400">a</kbd>
            </button>
            <button
              onClick={requestBulkResolve}
              disabled={bulkBusy}
              className="px-3 py-1.5 rounded-md bg-green-600 hover:bg-green-700 text-white disabled:opacity-50 font-medium"
            >
              Resolve <kbd className="ml-1 text-xs text-green-200">r</kbd>
            </button>
            <button
              onClick={clearSelection}
              className="px-3 py-1.5 rounded-md text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 font-medium"
            >
              Clear <kbd className="ml-1 text-xs text-gray-400">esc</kbd>
            </button>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 dark:bg-gray-700 text-white text-sm px-4 py-2.5 rounded-lg shadow-lg">
          {toast}
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
                  <th className="pl-5 pr-2 py-3 w-10">
                    <input
                      type="checkbox"
                      aria-label="Select all alerts"
                      className="rounded border-gray-300 dark:border-gray-600 cursor-pointer"
                      checked={allSelected}
                      ref={(el) => { if (el) el.indeterminate = someSelected }}
                      onChange={toggleAll}
                    />
                  </th>
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
                    selected.has(alert.id) && 'bg-blue-50/60 dark:bg-blue-900/20',
                    isDown && 'border-l-2 border-l-red-500',
                    isRecovery && 'border-l-2 border-l-green-500',
                  )}>
                    <td className="pl-5 pr-2 py-3" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select alert ${alert.id}`}
                        className="rounded border-gray-300 dark:border-gray-600 cursor-pointer"
                        checked={selected.has(alert.id)}
                        onChange={() => toggleSelect(alert.id)}
                      />
                    </td>
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
                          STATE_BADGE[displayState(alert)] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300')}
                          title={alert.is_acknowledged && alert.acknowledged_by ? `Acknowledged by ${alert.acknowledged_by}${alert.acknowledged_at ? ` ${new Date(alert.acknowledged_at).toLocaleString()}` : ''}` : undefined}>
                          {displayState(alert)}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 whitespace-nowrap">
                      {alert.state !== 'resolved' && (
                        <>
                          {!alert.is_acknowledged && (
                            <button
                              onClick={(e) => { e.stopPropagation(); handleAcknowledge(alert.id) }}
                              className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-800 font-medium mr-3"
                            >
                              Acknowledge
                            </button>
                          )}
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
                      <td colSpan={8} className="px-5 py-3 space-y-3">
                        {/* Header: device / severity / fired / state */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                          <div><span className="text-gray-400">Device:</span> <span className="text-gray-700 dark:text-gray-200">{alert.device || '—'}</span></div>
                          <div><span className="text-gray-400">Severity:</span> <span className="text-gray-700 dark:text-gray-200 capitalize">{sev}</span></div>
                          <div><span className="text-gray-400">Fired:</span> <span className="text-gray-700 dark:text-gray-200">{new Date(alert.fired_at).toLocaleString()}</span></div>
                          <div><span className="text-gray-400">State:</span> <span className="text-gray-700 dark:text-gray-200 capitalize">{displayState(alert)}</span>
                            {alert.is_acknowledged && alert.acknowledged_by && (
                              <span className="text-gray-400"> · by {alert.acknowledged_by}{alert.acknowledged_at ? ` ${new Date(alert.acknowledged_at).toLocaleString()}` : ''}</span>
                            )}
                          </div>
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

      {/* Bulk-resolve confirmation (5+ alerts) */}
      {showResolveConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setShowResolveConfirm(false)}>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Resolve {selected.size} alerts?</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
              This will mark {selected.size} alert{selected.size !== 1 ? 's' : ''} as resolved.
            </p>
            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setShowResolveConfirm(false)}
                className="px-4 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                onClick={doBulkResolve}
                disabled={bulkBusy}
                className="px-4 py-2 rounded-md text-sm font-medium bg-green-600 hover:bg-green-700 text-white disabled:opacity-50"
              >
                Resolve {selected.size} Alert{selected.size !== 1 ? 's' : ''}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

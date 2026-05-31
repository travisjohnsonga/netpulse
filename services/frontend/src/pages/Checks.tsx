import { Fragment, useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import EmptyState from '../components/EmptyState'
import Modal from '../components/Modal'
import {
  fetchChecks, fetchCheckSummary, saveCheck, deleteCheck, runCheckNow, fetchCheckResults,
  fetchDevices, fetchSites,
  type ServiceCheck, type CheckStatus, type CheckType, type CheckSummary,
  type ServiceCheckPayload, type CheckResultsResponse, type Device, type Site,
} from '../api/client'

const STATUS_BADGE: Record<CheckStatus, string> = {
  up: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  down: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  degraded: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}
const STATUS_DOT: Record<CheckStatus, string> = {
  up: 'bg-green-500', down: 'bg-red-500', degraded: 'bg-yellow-500', unknown: 'bg-gray-400',
}
// Implemented check types (handlers in apps/checks/runner.py).
const CHECK_TYPES: { id: CheckType; label: string }[] = [
  { id: 'https', label: 'HTTPS' },
  { id: 'http', label: 'HTTP' },
  { id: 'tcp', label: 'TCP' },
  { id: 'icmp', label: 'ICMP (ping)' },
  { id: 'dns', label: 'DNS' },
  { id: 'tls', label: 'TLS certificate' },
  { id: 'smtp', label: 'SMTP' },
  { id: 'ssh_banner', label: 'SSH banner' },
]

// Per-type default response-time thresholds (ms). TLS uses cert-day thresholds
// in config (warn_days/critical_days) instead, so it sets no latency thresholds.
const TYPE_RT_DEFAULTS: Partial<Record<CheckType, { warn: number; crit: number }>> = {
  icmp: { warn: 100, crit: 500 },
  dns: { warn: 500, crit: 2000 },
  smtp: { warn: 2000, crit: 5000 },
  ssh_banner: { warn: 500, crit: 2000 },
}

function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

// TLS day-remaining colour: green >30d, yellow 8–30d, red <7d.
function tlsDayClass(days: number): string {
  if (days <= 7) return 'text-red-600 dark:text-red-400'
  if (days <= 30) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-green-600 dark:text-green-400'
}

export default function Checks() {
  const [checks, setChecks] = useState<ServiceCheck[]>([])
  const [summary, setSummary] = useState<CheckSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [editCheck, setEditCheck] = useState<ServiceCheck | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000) }

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchChecks({ ordering: 'name' }), fetchCheckSummary()])
      .then(([c, s]) => { setChecks(c); setSummary(s); setError(null) })
      .catch(() => setError('Could not load service checks. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const handleRunNow = async (id: number) => {
    setBusyId(id)
    try { await runCheckNow(id); load() } finally { setBusyId(null) }
  }
  const handleDelete = async (id: number) => {
    if (!confirm('Delete this check?')) return
    setBusyId(id)
    try { await deleteCheck(id); load() } finally { setBusyId(null) }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Service Checks</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Agentless synthetic monitoring — NetPulse probes services externally.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
        >+ Add Check</button>
      </div>

      {toast && (
        <div className="bg-green-50 border border-green-200 dark:bg-green-900/30 dark:border-green-800 rounded-lg px-4 py-3 text-sm text-green-800 dark:text-green-300">{toast}</div>
      )}

      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
      )}

      {/* Summary bar */}
      {summary && (
        <div className="flex flex-wrap items-center gap-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3 text-sm">
          <span className="font-medium text-gray-700 dark:text-gray-200">{summary.total} checks</span>
          <span className="inline-flex items-center gap-1.5 text-green-600"><span className="w-2 h-2 rounded-full bg-green-500" />{summary.up} up</span>
          <span className="inline-flex items-center gap-1.5 text-red-600"><span className="w-2 h-2 rounded-full bg-red-500" />{summary.down} down</span>
          <span className="inline-flex items-center gap-1.5 text-yellow-600"><span className="w-2 h-2 rounded-full bg-yellow-500" />{summary.degraded} degraded</span>
          {summary.unknown > 0 && (
            <span className="inline-flex items-center gap-1.5 text-gray-500"><span className="w-2 h-2 rounded-full bg-gray-400" />{summary.unknown} unknown</span>
          )}
        </div>
      )}

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : checks.length === 0 ? (
          <EmptyState
            title="No service checks yet"
            description="Add an HTTP, HTTPS or TCP check to start monitoring a service externally."
            action={{ label: 'Add Check', onClick: () => setShowAdd(true) }}
            icon="✓"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Target</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Response</th>
                  <th className="px-5 py-3 font-medium">Checked</th>
                  <th className="px-5 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {checks.map((c) => {
                  const open = expandedId === c.id
                  return (
                  <Fragment key={c.id}>
                  <tr
                    onClick={() => setExpandedId(open ? null : c.id)}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer"
                  >
                    <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="text-gray-400 text-xs w-3 inline-block">{open ? '▼' : '▶'}</span>
                        {c.name}
                        {!c.is_enabled && <span className="ml-2 text-xs text-gray-400">(paused)</span>}
                      </span>
                    </td>
                    <td className="px-5 py-3 uppercase text-xs text-gray-500">{c.check_type}</td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-600 dark:text-gray-300">
                      {c.host}{c.effective_port ? `:${c.effective_port}` : ''}
                    </td>
                    <td className="px-5 py-3">
                      <span className="inline-flex items-center gap-1.5">
                        <span className={clsx('w-2 h-2 rounded-full', STATUS_DOT[c.current_status])} />
                        <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[c.current_status])}>
                          {c.current_status}
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-300">
                      {c.check_type === 'tls' && typeof c.last_details?.days_remaining === 'number'
                        ? <span className={clsx('font-semibold', tlsDayClass(c.last_details.days_remaining as number))}>
                            {c.last_details.days_remaining as number}d left
                          </span>
                        : c.check_type === 'icmp' && typeof c.last_details?.packet_loss_pct === 'number'
                          ? `${fmtMs(c.last_response_ms)} · ${c.last_details.packet_loss_pct as number}% loss`
                          : fmtMs(c.last_response_ms)}
                    </td>
                    <td className="px-5 py-3 text-gray-500 text-xs">
                      {c.last_checked ? new Date(c.last_checked).toLocaleTimeString() : 'never'}
                    </td>
                    <td className="px-5 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRunNow(c.id) }}
                        disabled={busyId === c.id}
                        className="text-blue-600 hover:text-blue-800 disabled:opacity-40 text-xs font-medium mr-3"
                      >Run now</button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setEditCheck(c) }}
                        className="text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 text-xs font-medium mr-3"
                      >Edit</button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(c.id) }}
                        disabled={busyId === c.id}
                        className="text-red-600 hover:text-red-800 disabled:opacity-40 text-xs font-medium"
                      >Delete</button>
                    </td>
                  </tr>
                  {open && (
                    <tr className="bg-gray-50/60 dark:bg-gray-900/40">
                      <td colSpan={7} className="px-5 py-4">
                        <CheckHistoryPanel
                          check={c}
                          busy={busyId === c.id}
                          onEdit={() => setEditCheck(c)}
                          onRunNow={() => handleRunNow(c.id)}
                          onDelete={() => handleDelete(c.id)}
                        />
                      </td>
                    </tr>
                  )}
                  </Fragment>
                )})}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showAdd && (
        <CheckModal
          onClose={() => setShowAdd(false)}
          onSaved={() => { setShowAdd(false); load(); showToast('Check created') }}
        />
      )}
      {editCheck && (
        <CheckModal
          check={editCheck}
          onClose={() => setEditCheck(null)}
          onSaved={() => { setEditCheck(null); load(); showToast('Check updated') }}
        />
      )}
    </div>
  )
}

// ── Expanding history panel ──────────────────────────────────────────────────

const HISTORY_PERIODS = ['1h', '6h', '24h', '7d'] as const
const TIMELINE_COLOR: Record<CheckStatus, string> = {
  up: 'bg-green-500', degraded: 'bg-yellow-500', down: 'bg-red-500', unknown: 'bg-gray-300 dark:bg-gray-600',
}

function uptimeClass(pct: number | null): string {
  if (pct == null) return 'text-gray-500'
  if (pct >= 99) return 'text-green-600 dark:text-green-400'
  if (pct >= 95) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-red-600 dark:text-red-400'
}

// Short, type-aware summary of a probe's details for the history table.
function formatResultDetails(type: CheckType, d: Record<string, unknown> | undefined): string {
  if (!d) return '—'
  const g = (k: string) => d[k]
  switch (type) {
    case 'http': case 'https': {
      const parts: string[] = []
      if (g('status_code') != null) parts.push(String(g('status_code')))
      if (g('body_match') != null) parts.push(g('body_match') ? 'body ✓' : 'body ✗')
      if (g('redirect_count')) parts.push(`${g('redirect_count')} redir`)
      return parts.join(' · ') || '—'
    }
    case 'icmp': return `${g('packet_loss_pct') ?? '?'}% loss · ${g('avg_rtt_ms') ?? '?'}ms · jitter ${g('jitter_ms') ?? '?'}ms`
    case 'dns': { const a = g('answers') as unknown[] | undefined; return a && a.length ? a.join(', ') : 'resolved' }
    case 'tls': return `${g('days_remaining') ?? '?'}d · ${g('cert_cn') ?? ''} (${g('issuer') ?? '?'})`
    case 'smtp': return `${g('starttls_supported') ? 'STARTTLS' : 'no STARTTLS'}${g('banner') ? ' · ' + String(g('banner')).slice(0, 40) : ''}`
    case 'ssh': case 'ssh_banner': return g('banner') ? String(g('banner')) : '—'
    case 'tcp': return g('matched') != null ? (g('matched') ? 'match ✓' : 'match ✗') : 'connected'
    default: return '—'
  }
}

function CheckHistoryPanel({ check, busy, onEdit, onRunNow, onDelete }: {
  check: ServiceCheck
  busy: boolean
  onEdit: () => void
  onRunNow: () => void
  onDelete: () => void
}) {
  const [period, setPeriod] = useState('24h')
  const [cache, setCache] = useState<Record<string, CheckResultsResponse>>({})
  const [loading, setLoading] = useState(false)
  const [shown, setShown] = useState(10)
  const data = cache[period]

  // Lazy-load on open / period change; cache per period.
  useEffect(() => {
    if (cache[period]) return
    let cancelled = false
    setLoading(true)
    fetchCheckResults(check.id, period)
      .then((r) => { if (!cancelled) setCache((c) => ({ ...c, [period]: r })) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [check.id, period, cache])
  useEffect(() => { setShown(10) }, [period])

  const results = data?.results ?? []
  const chrono = [...results].reverse()  // oldest → newest (left → right)
  const summary = data?.summary

  const rtOption: EChartsOption = {
    grid: { left: 44, right: 12, top: 10, bottom: 22 },
    tooltip: { trigger: 'axis', formatter: (p: any) => { const x = Array.isArray(p) ? p[0] : p; return `${x.axisValue}<br/>${x.value == null ? '—' : x.value + ' ms'}` } },
    xAxis: { type: 'category', data: chrono.map((r) => new Date(r.checked_at).toLocaleTimeString()), axisLabel: { fontSize: 9, showMaxLabel: true } },
    yAxis: { type: 'value', name: 'ms', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 } },
    series: [{
      type: 'line', smooth: true, showSymbol: false,
      data: chrono.map((r) => r.response_time_ms),
      lineStyle: { color: '#3b82f6', width: 1.5 }, areaStyle: { color: '#3b82f6', opacity: 0.1 },
      markLine: {
        silent: true, symbol: 'none',
        data: [
          ...(check.response_time_warning_ms ? [{ yAxis: check.response_time_warning_ms, lineStyle: { color: '#eab308', type: 'dashed' as const } }] : []),
          ...(check.response_time_critical_ms ? [{ yAxis: check.response_time_critical_ms, lineStyle: { color: '#ef4444', type: 'dashed' as const } }] : []),
        ],
      },
    }],
  }

  return (
    <div className="space-y-3">
      {/* Action button group */}
      <div className="flex gap-2">
        <button onClick={onEdit}
          className="px-3 py-1.5 text-xs font-medium border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800">Edit Check</button>
        <button onClick={onRunNow} disabled={busy}
          className="px-3 py-1.5 text-xs font-medium border border-blue-300 dark:border-blue-800 rounded-md text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950 disabled:opacity-40">Run Now</button>
        <button onClick={onDelete} disabled={busy}
          className="px-3 py-1.5 text-xs font-medium border border-red-300 dark:border-red-800 rounded-md text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-40">Delete</button>
      </div>

      {/* Period tabs + uptime */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1">
          {HISTORY_PERIODS.map((p) => (
            <button key={p} onClick={() => setPeriod(p)}
              className={clsx('px-2.5 py-1 text-xs rounded-md border',
                period === p ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                  : 'border-gray-200 dark:border-gray-700 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800')}>
              {p}
            </button>
          ))}
        </div>
        {summary && (
          <span className="text-sm">
            <span className={clsx('font-semibold', uptimeClass(summary.uptime_pct))}>
              {summary.uptime_pct == null ? '—' : `${summary.uptime_pct}%`}
            </span>
            <span className="text-gray-500 dark:text-gray-400"> uptime (last {period}) · {summary.total} checks</span>
          </span>
        )}
      </div>

      {loading && !data ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-24 bg-gray-200 dark:bg-gray-700 rounded" />
          <div className="h-5 bg-gray-200 dark:bg-gray-700 rounded" />
          <div className="h-20 bg-gray-200 dark:bg-gray-700 rounded" />
        </div>
      ) : results.length === 0 ? (
        <p className="text-sm text-gray-400 dark:text-gray-500 py-6 text-center">No results recorded in this period yet.</p>
      ) : (
        <>
          {/* Response time chart */}
          <div>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">Response time</p>
            <ReactECharts option={rtOption} style={{ height: 120 }} opts={{ renderer: 'svg' }} notMerge />
          </div>

          {/* Status timeline */}
          <div>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">Status timeline (oldest → newest)</p>
            <div className="flex gap-px h-5 rounded overflow-hidden">
              {chrono.map((r) => (
                <div key={r.id} className={clsx('flex-1 min-w-[2px]', TIMELINE_COLOR[r.status])}
                  title={`${r.status} · ${fmtMs(r.response_time_ms)} · ${new Date(r.checked_at).toLocaleString()}${r.error ? ' · ' + r.error : ''}`} />
              ))}
            </div>
            <div className="flex gap-3 mt-1 text-[10px] text-gray-400">
              <span><span className="inline-block w-2 h-2 rounded-sm bg-green-500 mr-1" />up</span>
              <span><span className="inline-block w-2 h-2 rounded-sm bg-yellow-500 mr-1" />degraded</span>
              <span><span className="inline-block w-2 h-2 rounded-sm bg-red-500 mr-1" />down</span>
            </div>
          </div>

          {/* Recent results table */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-3 py-1.5 font-medium">Time</th>
                  <th className="px-3 py-1.5 font-medium">Status</th>
                  <th className="px-3 py-1.5 font-medium">ms</th>
                  <th className="px-3 py-1.5 font-medium">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {results.slice(0, shown).map((r) => (
                  <tr key={r.id}>
                    <td className="px-3 py-1.5 text-gray-600 dark:text-gray-300 whitespace-nowrap">{new Date(r.checked_at).toLocaleTimeString()}</td>
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[r.status])} />
                        <span className="capitalize">{r.status}</span>
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-gray-600 dark:text-gray-300">{fmtMs(r.response_time_ms)}</td>
                    <td className="px-3 py-1.5 text-gray-500 dark:text-gray-400 max-w-xs truncate" title={r.error || formatResultDetails(check.check_type, r.details)}>
                      {r.error ? <span className="text-red-600 dark:text-red-400">{r.error}</span> : formatResultDetails(check.check_type, r.details)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between mt-2 text-xs text-gray-500 dark:text-gray-400">
              <span>Showing {Math.min(shown, results.length)} of {results.length} results</span>
              {shown < results.length && (
                <button onClick={() => setShown((n) => n + 20)} className="text-blue-600 hover:text-blue-800 font-medium">Load more</button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function CheckModal({ check, onClose, onSaved }: { check?: ServiceCheck; onClose: () => void; onSaved: () => void }) {
  const editing = !!check
  const [form, setForm] = useState<ServiceCheckPayload>(check ? {
    name: check.name, check_type: check.check_type, host: check.host, port: check.port,
    interval_seconds: check.interval_seconds, timeout_seconds: check.timeout_seconds,
    failures_before_alert: check.failures_before_alert,
    alert_on_down: check.alert_on_down, alert_on_recovery: check.alert_on_recovery,
    alert_on_degraded: check.alert_on_degraded,
    device: check.device, site: check.site, notes: check.notes,
    response_time_warning_ms: check.response_time_warning_ms,
    response_time_critical_ms: check.response_time_critical_ms,
  } : {
    name: '', check_type: 'https', host: '', interval_seconds: 60, timeout_seconds: 10,
    failures_before_alert: 2,
    alert_on_down: true, alert_on_recovery: true, alert_on_degraded: false,
  })
  // Free-form per-type config (path, query, warn_days, helo, …).
  const [cfg, setCfg] = useState<Record<string, unknown>>(check ? { ...(check.config || {}) } : { path: '/' })
  const [devices, setDevices] = useState<Device[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchDevices().then((r) => setDevices(r.results)).catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])

  const t = form.check_type
  const setCfgVal = (k: string, v: unknown) => setCfg((c) => ({ ...c, [k]: v }))

  const changeType = (next: CheckType) => {
    // check_type is immutable while editing (the select is disabled).
    if (editing) return
    setForm({ ...form, check_type: next })
    // Reset to sensible per-type config defaults.
    if (next === 'http' || next === 'https') setCfg({ path: '/' })
    else if (next === 'dns') setCfg({ record_type: 'A' })
    else if (next === 'tls') setCfg({ warn_days: 30, critical_days: 7 })
    else if (next === 'icmp') setCfg({ count: 4, packet_size: 56 })
    else if (next === 'smtp') setCfg({ helo: 'netpulse.local', starttls: false })
    else setCfg({})
  }

  const submit = async () => {
    setErr(null)
    if (!form.name?.trim() || !form.host?.trim()) { setErr('Name and host are required.'); return }
    setBusy(true)
    try {
      const payload: ServiceCheckPayload = { ...form, config: cleanConfig(cfg) }
      // On create, apply per-type response-time threshold defaults (TLS grades
      // on cert days, not latency). On edit, keep the check's existing thresholds.
      if (!editing) {
        const rt = TYPE_RT_DEFAULTS[t]
        if (rt) {
          payload.response_time_warning_ms = rt.warn
          payload.response_time_critical_ms = rt.crit
        }
      }
      await saveCheck(payload, check?.id)
      onSaved()
    } catch {
      setErr('Could not save the check.')
    } finally {
      setBusy(false)
    }
  }

  const input = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
  const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
  const num = (v: unknown) => (v == null || v === '' ? '' : String(v))

  return (
    <Modal
      title={editing ? 'Edit Check' : 'Add Service Check'}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={busy} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {busy ? 'Saving…' : editing ? 'Save Changes' : 'Save Check'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{err}</div>}
        <div>
          <label className={label}>Name</label>
          <input className={input} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Company Website" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Type {editing && <span className="text-gray-400">(fixed)</span>}</label>
            <select className={clsx(input, editing && 'opacity-60 cursor-not-allowed')} value={t} disabled={editing}
              onChange={(e) => changeType(e.target.value as CheckType)}>
              {CHECK_TYPES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Port {t === 'tls' || t === 'icmp' || t === 'dns' ? <span className="text-gray-400">(auto)</span> : <span className="text-gray-400">(optional)</span>}</label>
            <input type="number" className={input} disabled={t === 'icmp'} value={form.port ?? ''}
              onChange={(e) => setForm({ ...form, port: e.target.value ? Number(e.target.value) : null })} placeholder="auto" />
          </div>
        </div>
        <div>
          <label className={label}>{t === 'dns' ? 'Resolver host / target' : 'Host'}</label>
          <input className={input} value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })}
            placeholder={t === 'tls' ? 'api.example.com' : t === 'icmp' ? '10.0.0.1' : 'app.example.com'} />
        </div>

        {/* Type-specific config */}
        {(t === 'http' || t === 'https') && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Path</label>
              <input className={input} value={String(cfg.path ?? '/')} onChange={(e) => setCfgVal('path', e.target.value)} placeholder="/health" />
            </div>
            <div>
              <label className={label}>Method</label>
              <select className={input} value={String(cfg.method ?? 'GET')} onChange={(e) => setCfgVal('method', e.target.value)}>
                {['GET', 'HEAD', 'POST'].map((m) => <option key={m}>{m}</option>)}
              </select>
            </div>
            <div className="col-span-2">
              <label className={label}>Expected body contains <span className="text-gray-400">(optional)</span></label>
              <input className={input} value={String(cfg.expected_body ?? '')} onChange={(e) => setCfgVal('expected_body', e.target.value)} placeholder="OK" />
            </div>
            {t === 'https' && (
              <label className="col-span-2 inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={cfg.verify_ssl !== false} onChange={(e) => setCfgVal('verify_ssl', e.target.checked)} />
                Verify SSL certificate
              </label>
            )}
          </div>
        )}
        {t === 'tcp' && (
          <div className="grid grid-cols-2 gap-3">
            <div><label className={label}>Send <span className="text-gray-400">(optional)</span></label>
              <input className={input} value={String(cfg.send ?? '')} onChange={(e) => setCfgVal('send', e.target.value)} placeholder="PING\r\n" /></div>
            <div><label className={label}>Expect <span className="text-gray-400">(optional)</span></label>
              <input className={input} value={String(cfg.expect ?? '')} onChange={(e) => setCfgVal('expect', e.target.value)} placeholder="PONG" /></div>
          </div>
        )}
        {t === 'icmp' && (
          <div className="grid grid-cols-2 gap-3">
            <div><label className={label}>Ping count</label>
              <input type="number" className={input} value={num(cfg.count ?? 4)} onChange={(e) => setCfgVal('count', Number(e.target.value))} /></div>
            <div><label className={label}>Packet size (bytes)</label>
              <input type="number" className={input} value={num(cfg.packet_size ?? 56)} onChange={(e) => setCfgVal('packet_size', Number(e.target.value))} /></div>
          </div>
        )}
        {t === 'dns' && (
          <div className="grid grid-cols-2 gap-3">
            <div><label className={label}>Query name</label>
              <input className={input} value={String(cfg.query ?? '')} onChange={(e) => setCfgVal('query', e.target.value)} placeholder="company.com" /></div>
            <div><label className={label}>Record type</label>
              <select className={input} value={String(cfg.record_type ?? 'A')} onChange={(e) => setCfgVal('record_type', e.target.value)}>
                {['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS'].map((r) => <option key={r}>{r}</option>)}
              </select></div>
            <div><label className={label}>Expected answer <span className="text-gray-400">(optional)</span></label>
              <input className={input} value={String(cfg.expected_answer ?? '')} onChange={(e) => setCfgVal('expected_answer', e.target.value)} placeholder="1.2.3.4" /></div>
            <div><label className={label}>Nameserver <span className="text-gray-400">(optional)</span></label>
              <input className={input} value={String(cfg.nameserver ?? '')} onChange={(e) => setCfgVal('nameserver', e.target.value)} placeholder="8.8.8.8" /></div>
          </div>
        )}
        {t === 'tls' && (
          <div className="grid grid-cols-2 gap-3">
            <div><label className={label}>Warn days</label>
              <input type="number" className={input} value={num(cfg.warn_days ?? 30)} onChange={(e) => setCfgVal('warn_days', Number(e.target.value))} /></div>
            <div><label className={label}>Critical days</label>
              <input type="number" className={input} value={num(cfg.critical_days ?? 7)} onChange={(e) => setCfgVal('critical_days', Number(e.target.value))} /></div>
          </div>
        )}
        {t === 'smtp' && (
          <div className="grid grid-cols-2 gap-3">
            <div><label className={label}>HELO/EHLO name</label>
              <input className={input} value={String(cfg.helo ?? 'netpulse.local')} onChange={(e) => setCfgVal('helo', e.target.value)} /></div>
            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 mt-6">
              <input type="checkbox" checked={!!cfg.starttls} onChange={(e) => setCfgVal('starttls', e.target.checked)} /> Try STARTTLS
            </label>
          </div>
        )}
        {t === 'ssh_banner' && (
          <div>
            <label className={label}>Expected banner contains <span className="text-gray-400">(optional)</span></label>
            <input className={input} value={String(cfg.expected_banner ?? '')} onChange={(e) => setCfgVal('expected_banner', e.target.value)} placeholder="OpenSSH" />
          </div>
        )}

        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className={label}>Interval (s)</label>
            <input type="number" className={input} value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })} />
          </div>
          <div>
            <label className={label}>Timeout (s)</label>
            <input type="number" className={input} value={form.timeout_seconds} onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) })} />
          </div>
          <div>
            <label className={label}>Fails→alert</label>
            <input type="number" className={input} value={form.failures_before_alert} onChange={(e) => setForm({ ...form, failures_before_alert: Number(e.target.value) })} />
          </div>
        </div>

        {/* Alert toggles */}
        <div>
          <label className={label}>Alerts</label>
          <div className="flex flex-wrap gap-4 text-sm text-gray-700 dark:text-gray-300">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={form.alert_on_down ?? true} onChange={(e) => setForm({ ...form, alert_on_down: e.target.checked })} />
              On down
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={form.alert_on_recovery ?? true} onChange={(e) => setForm({ ...form, alert_on_recovery: e.target.checked })} />
              On recovery
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={form.alert_on_degraded ?? false} onChange={(e) => setForm({ ...form, alert_on_degraded: e.target.checked })} />
              On degraded
            </label>
          </div>
        </div>

        {/* Optional associations */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Device <span className="text-gray-400">(optional)</span></label>
            <select className={input} value={form.device ?? ''}
              onChange={(e) => setForm({ ...form, device: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— none —</option>
              {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Site <span className="text-gray-400">(optional)</span></label>
            <select className={input} value={form.site ?? ''}
              onChange={(e) => setForm({ ...form, site: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— none —</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className={label}>Notes <span className="text-gray-400">(optional)</span></label>
          <textarea className={input} rows={2} value={form.notes ?? ''} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </div>
      </div>
    </Modal>
  )
}

// Drop empty optional config keys so the backend stores a tidy config.
function cleanConfig(cfg: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(cfg)) {
    if (v === '' || v == null) continue
    out[k] = v
  }
  return out
}

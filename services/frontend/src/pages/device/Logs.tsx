import { useCallback, useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { fetchLogs, type DeviceDetail, type LogEntry } from '../../api/client'
import { SEVERITY_ORDER, TIME_RANGES, rangeFrom, severityBadge } from '../../lib/severity'
import { usePreferencesStore } from '../../store/preferencesStore'

const DEFAULT_PAGE_SIZE = 50

export default function Logs({ device }: { device: DeviceDetail }) {
  const [severities, setSeverities] = useState<Set<string>>(new Set())
  const [range, setRange] = useState('1h')
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [search, setSearch] = useState('')
  const [rows, setRows] = useState<LogEntry[]>([])
  const [count, setCount] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [auto, setAuto] = useState(false)

  // Apply user preference defaults once loaded.
  const prefs = usePreferencesStore((s) => s.prefs)
  const prefsApplied = useRef(false)
  useEffect(() => {
    if (!prefs || prefsApplied.current) return
    prefsApplied.current = true
    setRange(prefs.log_default_time_range)
    setPageSize(prefs.log_default_page_size)
    setAuto(prefs.log_auto_refresh)
  }, [prefs])

  const load = useCallback(async (pg: number, append: boolean) => {
    setLoading(true); setError(null)
    const params: Record<string, string> = {
      device_hostname: device.hostname, page: String(pg), page_size: String(pageSize),
    }
    if (severities.size) params.severity = [...severities].join(',')
    const from = rangeFrom(range)
    if (from) params.from = from
    if (search.trim()) params.search = search.trim()
    try {
      const res = await fetchLogs(params)
      setCount(res.count)
      setRows((prev) => (append ? [...prev, ...res.results] : res.results))
      if (res.error) setError(res.error)
    } catch { setError('Failed to load logs.') } finally { setLoading(false) }
  }, [device.hostname, severities, range, search, pageSize])

  // Reload from page 1 when filters change (debounced for search).
  useEffect(() => {
    const t = setTimeout(() => { setPage(1); load(1, false) }, 300)
    return () => clearTimeout(t)
  }, [load])

  // Auto-refresh.
  const autoRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => {
    if (autoRef.current) clearInterval(autoRef.current)
    if (auto) autoRef.current = setInterval(() => { setPage(1); load(1, false) }, 30000)
    return () => { if (autoRef.current) clearInterval(autoRef.current) }
  }, [auto, load])

  const toggleSev = (s: string) => setSeverities((prev) => { const n = new Set(prev); n.has(s) ? n.delete(s) : n.add(s); return n })
  const loadMore = () => { const next = page + 1; setPage(next); load(next, true) }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Device Logs — {device.hostname}</h3>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-refresh 30s
          </label>
          <button onClick={() => { setPage(1); load(1, false) }} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50">🔄 Refresh</button>
        </div>
      </div>

      {/* Filters */}
      <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-700 space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500 dark:text-gray-400 mr-1">Severity:</span>
          {SEVERITY_ORDER.map((s) => (
            <button key={s} onClick={() => toggleSev(s)}
              className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize border',
                severities.has(s) ? severityBadge(s) + ' border-transparent' : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50')}>
              {s}
            </button>
          ))}
          {severities.size > 0 && <button onClick={() => setSeverities(new Set())} className="text-xs text-blue-600 ml-1">clear</button>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={range} onChange={(e) => setRange(e.target.value)} className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100">
            {TIME_RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter by message text…"
            className="flex-1 min-w-[12rem] px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
      </div>

      {error && <div className="bg-yellow-50 border-b border-yellow-200 px-4 py-2 text-sm text-yellow-800">{error}</div>}

      <LogRows rows={rows} loading={loading && page === 1} />

      <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
        <span>{rows.length} of {count} shown</span>
        {rows.length < count && (
          <button onClick={loadMore} disabled={loading} className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50">
            {loading ? 'Loading…' : 'Load more'}
          </button>
        )}
      </div>
    </div>
  )
}

function LogRows({ rows, loading }: { rows: LogEntry[]; loading: boolean }) {
  if (loading) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (rows.length === 0) return <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-500">No log messages match these filters.</p>
  return (
    <div className="overflow-x-auto max-h-[30rem]">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-gray-50 dark:bg-gray-900/50">
          <tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-4 py-2 font-medium w-40">Time</th>
            <th className="px-4 py-2 font-medium w-24">Severity</th>
            <th className="px-4 py-2 font-medium w-24">Facility</th>
            <th className="px-4 py-2 font-medium">Message</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 align-top">
              <td className="px-4 py-1.5 text-gray-500 dark:text-gray-400 font-mono text-xs whitespace-nowrap">{new Date(r.timestamp).toLocaleString()}</td>
              <td className="px-4 py-1.5"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', severityBadge(r.severity))}>{r.severity}</span></td>
              <td className="px-4 py-1.5 text-gray-500 dark:text-gray-400 text-xs uppercase">{r.facility || '—'}</td>
              <td className="px-4 py-1.5 text-gray-700 dark:text-gray-300">{r.program && <span className="text-gray-400 dark:text-gray-500">{r.program}: </span>}{r.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

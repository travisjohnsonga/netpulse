import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchLogs, fetchDevices, fetchSites,
  type LogEntry, type Device, type Site,
} from '../api/client'
import { SEVERITY_ORDER, TIME_RANGES, rangeFrom, severityBadge } from '../lib/severity'

const PAGE_SIZE = 50
const ROLES = ['access', 'distribution', 'core', 'wan-edge', 'firewall']
const selCls = 'px-3 py-1.5 text-sm border border-gray-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500'

export default function Logs() {
  const navigate = useNavigate()
  const [devices, setDevices] = useState<Device[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [deviceHost, setDeviceHost] = useState('')
  const [site, setSite] = useState('')
  const [role, setRole] = useState('')
  const [severities, setSeverities] = useState<Set<string>>(new Set())
  const [range, setRange] = useState('1h')
  const [search, setSearch] = useState('')

  const [rows, setRows] = useState<LogEntry[]>([])
  const [count, setCount] = useState(0)
  const [summary, setSummary] = useState<Record<string, number>>({})
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [auto, setAuto] = useState(false)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])

  const hostToId = useMemo(() => Object.fromEntries(devices.map((d) => [d.hostname, d.id])), [devices])

  const load = useCallback(async (pg: number, append: boolean) => {
    setLoading(true); setError(null)
    const params: Record<string, string> = { page: String(pg), page_size: String(PAGE_SIZE) }
    if (deviceHost) params.device_hostname = deviceHost
    if (site) params.site = site
    if (role) params.role = role
    if (severities.size) params.severity = [...severities].join(',')
    const from = rangeFrom(range)
    if (from) params.from = from
    if (search.trim()) params.search = search.trim()
    try {
      const res = await fetchLogs(params)
      setCount(res.count); setSummary(res.summary.by_severity)
      setRows((prev) => (append ? [...prev, ...res.results] : res.results))
      if (res.error) setError(res.error)
    } catch { setError('Failed to load logs.') } finally { setLoading(false) }
  }, [deviceHost, site, role, severities, range, search])

  useEffect(() => {
    const t = setTimeout(() => { setPage(1); load(1, false) }, 300)
    return () => clearTimeout(t)
  }, [load])

  const autoRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => {
    if (autoRef.current) clearInterval(autoRef.current)
    if (auto) autoRef.current = setInterval(() => { setPage(1); load(1, false) }, 30000)
    return () => { if (autoRef.current) clearInterval(autoRef.current) }
  }, [auto, load])

  const toggleSev = (s: string) => setSeverities((p) => { const n = new Set(p); n.has(s) ? n.delete(s) : n.add(s); return n })
  const loadMore = () => { const next = page + 1; setPage(next); load(next, true) }

  const exportCsv = () => {
    const header = 'time,hostname,severity,facility,program,message\n'
    const body = rows.map((r) => [r.timestamp, r.hostname, r.severity, r.facility, r.program,
      `"${(r.message || '').replace(/"/g, '""')}"`].join(',')).join('\n')
    const blob = new Blob([header + body], { type: 'text/csv' })
    const url = URL.createObjectURL(blob); const a = document.createElement('a')
    a.href = url; a.download = 'netpulse-logs.csv'; a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Network Logs</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-600"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-refresh 30s</label>
          <button onClick={exportCsv} disabled={!rows.length} className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50">Export</button>
          <button onClick={() => { setPage(1); load(1, false) }} className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">🔄</button>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 space-y-3">
        <div className="flex flex-wrap gap-2">
          <select className={selCls} value={deviceHost} onChange={(e) => setDeviceHost(e.target.value)}>
            <option value="">All Devices</option>
            {devices.map((d) => <option key={d.id} value={d.hostname}>{d.hostname}</option>)}
          </select>
          <select className={selCls} value={site} onChange={(e) => setSite(e.target.value)}>
            <option value="">All Sites</option>
            {sites.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
          </select>
          <select className={selCls} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">All Roles</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select className={selCls} value={range} onChange={(e) => setRange(e.target.value)}>
            {TIME_RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
          <input className="flex-1 min-w-[12rem] px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search message text…" />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500 mr-1">Severity:</span>
          {SEVERITY_ORDER.map((s) => (
            <button key={s} onClick={() => toggleSev(s)}
              className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize border',
                severities.has(s) ? severityBadge(s) + ' border-transparent' : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50')}>{s}</button>
          ))}
          {severities.size > 0 && <button onClick={() => setSeverities(new Set())} className="text-xs text-blue-600 ml-1">clear</button>}
        </div>
      </div>

      {/* Summary bar */}
      <div className="flex flex-wrap gap-3 text-sm">
        <span className="font-medium text-gray-700">{count.toLocaleString()} messages</span>
        {(['critical', 'error', 'warning', 'info'] as const).map((s) => (
          <span key={s} className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', severityBadge(s))}>
            {(summary[s] ?? 0).toLocaleString()} {s}
          </span>
        ))}
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-2 text-sm text-yellow-800">{error}</div>}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        {loading && page === 1 ? (
          <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : rows.length === 0 ? (
          <p className="py-12 text-center text-sm text-gray-400">No log messages match these filters.</p>
        ) : (
          <div className="overflow-x-auto max-h-[34rem]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-50">
                <tr className="text-gray-500 text-left border-b border-gray-200">
                  <th className="px-4 py-2 font-medium w-40">Time</th>
                  <th className="px-4 py-2 font-medium w-32">Device</th>
                  <th className="px-4 py-2 font-medium w-24">Severity</th>
                  <th className="px-4 py-2 font-medium">Message</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr onClick={() => setExpanded(expanded === r.id ? null : r.id)} className="hover:bg-gray-50 cursor-pointer align-top">
                      <td className="px-4 py-1.5 text-gray-500 font-mono text-xs whitespace-nowrap">{new Date(r.timestamp).toLocaleString()}</td>
                      <td className="px-4 py-1.5">
                        <button onClick={(e) => { e.stopPropagation(); const id = hostToId[r.hostname]; if (id) navigate(`/devices/${id}`) }}
                          className="text-blue-600 hover:text-blue-800 font-medium">{r.hostname}</button>
                      </td>
                      <td className="px-4 py-1.5"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', severityBadge(r.severity))}>{r.severity}</span></td>
                      <td className="px-4 py-1.5 text-gray-700 truncate max-w-0">{r.program && <span className="text-gray-400">{r.program}: </span>}{r.message}</td>
                    </tr>
                    {expanded === r.id && (
                      <tr className="bg-gray-50">
                        <td colSpan={4} className="px-4 py-3 text-xs text-gray-600">
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
                            <div><span className="text-gray-400">Facility:</span> {r.facility || '—'}</div>
                            <div><span className="text-gray-400">Program:</span> {r.program || '—'}</div>
                            <div><span className="text-gray-400">PID:</span> {r.pid || '—'}</div>
                            <div><span className="text-gray-400">Source IP:</span> {r.source_ip || '—'}</div>
                          </div>
                          <pre className="bg-gray-900 text-gray-100 rounded-md p-2 overflow-x-auto whitespace-pre-wrap">{r.raw || r.message}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 text-xs text-gray-500">
          <span>{rows.length} of {count.toLocaleString()} shown</span>
          {rows.length < count && <button onClick={loadMore} disabled={loading} className="px-3 py-1.5 border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50">{loading ? 'Loading…' : 'Load more'}</button>}
        </div>
      </div>
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { fetchServers, fetchPingSummary, type Server, type PingSummary } from '../api/client'
import PingSparkline, { pingColor } from '../components/PingSparkline'
import { useSite } from '../store/siteStore'
import { INPUT, SELECT } from '../lib/ui'
import StatusBadge from '../components/StatusBadge'
import StatCard from '../components/StatCard'
import { compactAgo } from '../lib/time'
import { STRIPED_ROW } from '../lib/tableStyles'

// last_seen older than this (ms) with no fresh heartbeat → offline.
const OFFLINE_MS = 5 * 60 * 1000

function isOnline(s: Server): boolean {
  // Prefer the server's authoritative is_online (same threshold the liveness
  // alert uses) so the badge agrees with alerting; fall back to a client-side
  // window for older API responses.
  if (typeof s.is_online === 'boolean') return s.is_online
  return s.status === 'active' && !!s.last_seen &&
    Date.now() - new Date(s.last_seen).getTime() < OFFLINE_MS
}

function serverState(s: Server): 'online' | 'offline' | 'degraded' {
  if (!isOnline(s)) return 'offline'
  const m = s.latest_metrics
  const hot = [m.cpu_pct, m.memory_pct, m.disk_max_pct].some((v) => v != null && v >= 80)
  return hot ? 'degraded' : 'online'
}

function barColor(pct: number | null | undefined): string {
  if (pct == null) return 'bg-gray-300 dark:bg-gray-600'
  if (pct >= 80) return 'bg-red-500'
  if (pct >= 60) return 'bg-amber-500'
  return 'bg-green-500'
}

function Bar({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="text-xs text-gray-400">—</span>
  return (
    <div className="flex items-center gap-2 min-w-[7rem]">
      <div className="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <div className={`h-full ${barColor(pct)}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <span className="text-xs tabular-nums w-9 text-right text-gray-600 dark:text-gray-300">{Math.round(pct)}%</span>
    </div>
  )
}

// Collector ping/RTT cell — mirrors the Devices list (ms + sparkline). A server
// with no routable IP (synthetic device record) simply has no ping data → "—",
// never a false "unreachable".
function Ping({ p }: { p?: PingSummary }) {
  const ms = p?.current_ms ?? null
  const color = pingColor(ms)
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-xs tabular-nums w-12" style={ms != null ? { color } : undefined}>
        {ms != null ? `${ms}ms` : <span className="text-gray-300 dark:text-gray-600">—</span>}
      </span>
      {p?.sparkline?.length ? (
        <span className="hidden sm:inline-block"><PingSparkline data={p.sparkline} color={color} /></span>
      ) : null}
    </span>
  )
}


export default function Servers() {
  const nav = useNavigate()
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [osFilter, setOsFilter] = useState('All')
  const [roleFilter, setRoleFilter] = useState('All')
  const [statusFilter, setStatusFilter] = useState('All')
  // Site scoping comes from the global header selector.
  const { selectedSite } = useSite()

  // Collector-originated ping/RTT, keyed by device_id — the SAME summary the
  // Devices list uses (the monitor now writes device_reachability for agent
  // hosts too), so servers get the device-style ping column + sparkline.
  const [ping, setPing] = useState<Record<number, PingSummary>>({})

  useEffect(() => {
    fetchServers().then(setServers).catch(() => setError('Failed to load servers.')).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    let active = true
    const tick = () => fetchPingSummary()
      .then((rows) => { if (active) setPing(Object.fromEntries(rows.map((r) => [r.device_id, r]))) })
      .catch(() => {})
    tick()
    const id = setInterval(tick, 60_000)
    return () => { active = false; clearInterval(id) }
  }, [])

  // Servers at the active site (summary cards + filters scope to this set).
  const siteScoped = useMemo(
    () => (selectedSite ? servers.filter((s) => String(s.site?.id) === selectedSite) : servers),
    [servers, selectedSite],
  )

  const osOptions = useMemo(() => ['All', ...new Set(siteScoped.map((s) => s.os).filter(Boolean))], [siteScoped])
  const roleOptions = useMemo(() => ['All', ...new Set(siteScoped.flatMap((s) => s.roles))], [siteScoped])

  const filtered = useMemo(() => siteScoped.filter((s) => {
    if (search && !s.hostname.toLowerCase().includes(search.toLowerCase())) return false
    if (osFilter !== 'All' && s.os !== osFilter) return false
    if (roleFilter !== 'All' && !s.roles.includes(roleFilter)) return false
    if (statusFilter !== 'All' && serverState(s) !== statusFilter) return false
    return true
  }), [siteScoped, search, osFilter, roleFilter, statusFilter])

  const online = siteScoped.filter(isOnline).length
  const offline = siteScoped.length - online

  if (loading) return <div className="p-6 text-gray-500">Loading servers…</div>

  if (!servers.length) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-6">Servers</h1>
        <div className="max-w-lg mx-auto mt-12 text-center border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl p-10">
          <div className="text-4xl mb-3">🖥️</div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">No servers monitored yet</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">
            Install the spane Agent on your Linux or Windows servers to see CPU,
            memory, disk and service metrics here.
          </p>
          <Link to="/settings/agents" className="inline-block px-4 py-2 text-sm bg-blue-600 text-white rounded-lg">
            Go to Settings → Agents
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Servers</h1>
      {error && <div className="mb-3 text-sm text-red-600">{error}</div>}

      {/* Count-based summary (how many are down is the actionable question; a
          fleet CPU/mem average hides individual hosts in trouble). */}
      <div className="grid grid-cols-3 gap-3 mb-5">
        <StatCard title="Total Servers" value={siteScoped.length} color="blue" />
        <StatCard title="Up" value={online} color="green" />
        <StatCard title="Down" value={offline} color="red" />
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search hostname…"
          className={`${INPUT} flex-1 min-w-[12rem]`} />
        {[['OS', osFilter, setOsFilter, osOptions], ['Role', roleFilter, setRoleFilter, roleOptions],
          ['Status', statusFilter, setStatusFilter, ['All', 'online', 'degraded', 'offline']]].map(([label, val, setter, opts]) => (
          <select key={label as string} value={val as string} onChange={(e) => (setter as (v: string) => void)(e.target.value)}
            className={SELECT}>
            {(opts as string[]).map((o) => <option key={o} value={o}>{o === 'All' ? `${label}: All` : o}</option>)}
          </select>
        ))}
      </div>

      <div className="overflow-x-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
            <tr>
              {['Hostname', 'OS', 'Ping', 'CPU', 'Memory', 'Disk', 'Load', 'Roles', 'Last Change', 'Status'].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const m = s.latest_metrics
              const up = isOnline(s)
              return (
                <tr key={s.id} onClick={() => nav(`/servers/${s.id}`)}
                  className={`cursor-pointer ${STRIPED_ROW}`}>
                  <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100">{s.hostname}</td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">{s.os_name || s.os || '—'}</td>
                  <td className="px-3 py-2"><Ping p={s.device_id != null ? ping[s.device_id] : undefined} /></td>
                  <td className="px-3 py-2"><Bar pct={m.cpu_pct} /></td>
                  <td className="px-3 py-2"><Bar pct={m.memory_pct} /></td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">
                    {m.disk_max_pct == null ? '—' : (
                      <span className={m.disk_max_pct >= 80 ? 'text-red-600 dark:text-red-400' : ''}>
                        {m.disk_max_mount} {Math.round(m.disk_max_pct)}%{m.disk_max_pct >= 80 ? ' ⚠️' : ''}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-600 dark:text-gray-300">
                    {m.load_1 == null ? '—' : m.load_1.toFixed(2)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {s.roles.length ? s.roles.map((r) => (
                        <span key={r} className="px-1.5 py-0.5 text-[10px] uppercase rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">{r}</span>
                      )) : <span className="text-xs text-gray-400">—</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-500 dark:text-gray-400">{compactAgo(s.last_seen)}</td>
                  <td className="px-3 py-2"><StatusBadge up={up} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!filtered.length && <div className="p-6 text-center text-sm text-gray-500">No servers match the filters.</div>}
      </div>
    </div>
  )
}

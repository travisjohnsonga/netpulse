import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { fetchServers, fetchPingSummary, type Server, type PingSummary } from '../api/client'
import { useSite } from '../store/siteStore'
import { INPUT, SELECT } from '../lib/ui'
import StatCard from '../components/StatCard'
import ColumnPicker from '../components/ColumnPicker'
import { STRIPED_ROW, STICKY_COL, STICKY_COL_HEAD } from '../lib/tableStyles'
import {
  SERVER_COLUMNS, defaultServerColumnKeys, loadServerColumnKeys, saveServerColumnKeys,
  type ServerColCtx,
} from '../lib/serverColumns'
import { sshUrl, rdpUrl, sshTooltip } from '../lib/ssh'

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

// Protocol-aware connect: RDP for Windows hosts, SSH for Linux. The agent reports
// os = os_family ("windows"/"linux"); target host = the agent's reporting IP.
function ConnectAction({ s }: { s: Server }) {
  const host = s.last_ip
  if (!host) return <span className="text-gray-300 dark:text-gray-500">—</span>
  const win = (s.os || '').toLowerCase().startsWith('win')
  const href = win ? rdpUrl(host) : sshUrl({ ip_address: host })
  return (
    <a
      href={href}
      onClick={(e) => e.stopPropagation()}
      target="_blank" rel="noopener noreferrer"
      title={win ? `RDP to ${s.hostname} (${host})` : sshTooltip(s.hostname, { ip_address: host })}
      className="text-sm font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400"
    >
      {win ? 'RDP' : 'SSH'}
    </a>
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
  // Column show/hide/reorder (same control + persistence model as the Devices list).
  const [columnKeys, setColumnKeys] = useState<string[]>(loadServerColumnKeys)
  const setColumns = (keys: string[]) => { setColumnKeys(keys); saveServerColumnKeys(keys) }
  const resetColumns = () => { setColumnKeys(defaultServerColumnKeys()) }
  const activeColumns = useMemo(
    () => columnKeys.map((k) => SERVER_COLUMNS.find((c) => c.key === k)).filter(Boolean) as typeof SERVER_COLUMNS,
    [columnKeys],
  )
  const colCtx: ServerColCtx = { ping }

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
          <p className="text-sm text-gray-500 dark:text-gray-300 mb-5">
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
        <ColumnPicker activeKeys={columnKeys} onChange={setColumns} onReset={resetColumns} columns={SERVER_COLUMNS} />
      </div>

      <div className="overflow-x-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
            <tr>
              {activeColumns.map((col, i) => (
                <th key={col.key}
                  className={`px-3 py-2 font-medium whitespace-nowrap ${i === 0 ? STICKY_COL_HEAD : ''}`}>{col.label}</th>
              ))}
              <th className="px-3 py-2 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.id} onClick={() => nav(`/servers/${s.id}`)}
                className={`cursor-pointer ${STRIPED_ROW}`}>
                {activeColumns.map((col, i) => (
                  <td key={col.key} className={`px-3 py-2 whitespace-nowrap ${i === 0 ? STICKY_COL : ''}`}>{col.render(s, colCtx)}</td>
                ))}
                {/* Protocol-aware connect (RDP for Windows, SSH for Linux). */}
                <td className="px-3 py-2 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  <ConnectAction s={s} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!filtered.length && <div className="p-6 text-center text-sm text-gray-500">No servers match the filters.</div>}
      </div>
    </div>
  )
}

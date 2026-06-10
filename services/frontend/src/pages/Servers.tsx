import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { fetchServers, type Server } from '../api/client'

// last_seen older than this (ms) with no fresh heartbeat → offline.
const OFFLINE_MS = 5 * 60 * 1000

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function isOnline(s: Server): boolean {
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

const STATE_BADGE: Record<string, string> = {
  online: 'text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/40',
  offline: 'text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/40',
  degraded: 'text-amber-700 bg-amber-100 dark:text-amber-300 dark:bg-amber-900/40',
}
const STATE_LABEL: Record<string, string> = { online: '✅ Online', offline: '🔴 Offline', degraded: '⚠️ Degraded' }

export default function Servers() {
  const nav = useNavigate()
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [osFilter, setOsFilter] = useState('All')
  const [roleFilter, setRoleFilter] = useState('All')
  const [statusFilter, setStatusFilter] = useState('All')
  const [siteFilter, setSiteFilter] = useState('All')

  useEffect(() => {
    fetchServers().then(setServers).catch(() => setError('Failed to load servers.')).finally(() => setLoading(false))
  }, [])

  const osOptions = useMemo(() => ['All', ...new Set(servers.map((s) => s.os).filter(Boolean))], [servers])
  const roleOptions = useMemo(() => ['All', ...new Set(servers.flatMap((s) => s.roles))], [servers])
  const siteOptions = useMemo(() => ['All', ...new Set(servers.map((s) => s.site?.name).filter(Boolean) as string[])], [servers])

  const filtered = useMemo(() => servers.filter((s) => {
    if (search && !s.hostname.toLowerCase().includes(search.toLowerCase())) return false
    if (osFilter !== 'All' && s.os !== osFilter) return false
    if (roleFilter !== 'All' && !s.roles.includes(roleFilter)) return false
    if (siteFilter !== 'All' && s.site?.name !== siteFilter) return false
    if (statusFilter !== 'All' && serverState(s) !== statusFilter) return false
    return true
  }), [servers, search, osFilter, roleFilter, statusFilter, siteFilter])

  const online = servers.filter(isOnline).length
  const avg = (vals: (number | null)[]) => {
    const nums = vals.filter((v): v is number => v != null)
    return nums.length ? Math.round(nums.reduce((a, b) => a + b, 0) / nums.length) : null
  }
  const cpuAvg = avg(servers.map((s) => s.latest_metrics.cpu_pct))
  const memAvg = avg(servers.map((s) => s.latest_metrics.memory_pct))

  if (loading) return <div className="p-6 text-gray-500">Loading servers…</div>

  if (!servers.length) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-6">Servers</h1>
        <div className="max-w-lg mx-auto mt-12 text-center border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl p-10">
          <div className="text-4xl mb-3">🖥️</div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">No servers monitored yet</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">
            Install the NetPulse Agent on your Linux or Windows servers to see CPU,
            memory, disk and service metrics here.
          </p>
          <Link to="/settings/agents" className="inline-block px-4 py-2 text-sm bg-blue-600 text-white rounded-lg">
            Go to Settings → Agents
          </Link>
        </div>
      </div>
    )
  }

  const cards = [
    { label: 'Total Servers', value: servers.length },
    { label: 'Online', value: `${online} / ${servers.length - online}` },
    { label: 'CPU Avg', value: cpuAvg == null ? '—' : `${cpuAvg}%` },
    { label: 'Mem Avg', value: memAvg == null ? '—' : `${memAvg}%` },
  ]

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Servers</h1>
      {error && <div className="mb-3 text-sm text-red-600">{error}</div>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        {cards.map((c) => (
          <div key={c.label} className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-xs text-gray-500 dark:text-gray-400">{c.label}</div>
            <div className="text-2xl font-bold text-gray-900 dark:text-gray-100 mt-1">{c.value}</div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search hostname…"
          className="px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600 flex-1 min-w-[12rem]" />
        {[['OS', osFilter, setOsFilter, osOptions], ['Role', roleFilter, setRoleFilter, roleOptions],
          ['Status', statusFilter, setStatusFilter, ['All', 'online', 'degraded', 'offline']],
          ['Site', siteFilter, setSiteFilter, siteOptions]].map(([label, val, setter, opts]) => (
          <select key={label as string} value={val as string} onChange={(e) => (setter as (v: string) => void)(e.target.value)}
            className="px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600">
            {(opts as string[]).map((o) => <option key={o} value={o}>{o === 'All' ? `${label}: All` : o}</option>)}
          </select>
        ))}
      </div>

      <div className="overflow-x-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
            <tr>
              {['Hostname', 'OS', 'CPU', 'Memory', 'Disk', 'Load', 'Roles', 'Last Seen', 'Status'].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const m = s.latest_metrics
              const state = serverState(s)
              return (
                <tr key={s.id} onClick={() => nav(`/servers/${s.id}`)}
                  className="border-b dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/30 cursor-pointer">
                  <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100">{s.hostname}</td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">{s.os_version || s.os || '—'}</td>
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
                  <td className="px-3 py-2 text-gray-500 dark:text-gray-400">{timeAgo(s.last_seen)}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 text-xs rounded-full ${STATE_BADGE[state]}`}>{STATE_LABEL[state]}</span>
                  </td>
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

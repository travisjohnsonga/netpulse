import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import TimeRangeSelector, { RANGE_LABEL, type TimeRange } from '../components/TimeRangeSelector'
import {
  fetchServer, fetchServerMetricHistory, fetchServerRoleAssignments,
  assignServerRole, removeServerRole, detectServerRoles, fetchServerRoles,
  type ServerDetail as ServerDetailT, type MetricHistory,
  type AssignedRole, type DetectedRole, type ServerRole,
} from '../api/client'

const TABS = ['Overview', 'CPU', 'Memory', 'Disk', 'Network', 'Processes', 'Services', 'Roles', 'Logs', 'Alerts'] as const
type Tab = typeof TABS[number]

function fmtBytes(n?: number | null): string {
  if (n == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}
function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
const color = (p?: number | null) => p == null ? 'bg-gray-300 dark:bg-gray-600' : p >= 80 ? 'bg-red-500' : p >= 60 ? 'bg-amber-500' : 'bg-green-500'

function MetricCard({ label, pct, sub }: { label: string; pct: number | null; sub?: string }) {
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
      <div className="text-2xl font-bold text-gray-900 dark:text-gray-100 mt-1">{pct == null ? '—' : `${Math.round(pct)}%`}</div>
      <div className="mt-2 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <div className={`h-full ${color(pct)}`} style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
      </div>
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </div>
  )
}

function LineChart({ history, fields, height = 260 }: { history?: MetricHistory; fields: string[]; height?: number }) {
  const series = history?.series ?? []
  if (!series.length) return <div className="text-sm text-gray-500 py-10 text-center">No time-series data yet.</div>
  const times = [...new Set(series.map((r) => r.t as string))].sort()
  const option: EChartsOption = {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: '#9ca3af' } },
    grid: { left: 48, right: 16, top: 16, bottom: 36 },
    xAxis: { type: 'category', data: times.map((t) => new Date(t).toLocaleTimeString()), axisLabel: { color: '#9ca3af' } },
    yAxis: { type: 'value', axisLabel: { color: '#9ca3af' } },
    series: fields.map((f) => ({
      name: f, type: 'line', smooth: true, showSymbol: false, areaStyle: { opacity: 0.08 },
      data: times.map((t) => { const row = series.find((r) => r.t === t); return row ? (row[f] as number ?? null) : null }),
    })),
  }
  return <ReactECharts option={option} style={{ height }} opts={{ renderer: 'svg' }} notMerge />
}

function useHistory(id: string, metric: string, active: boolean, range: TimeRange) {
  const [data, setData] = useState<MetricHistory>()
  useEffect(() => {
    if (!active) return
    fetchServerMetricHistory(id, metric, range).then(setData).catch(() => setData(undefined))
  }, [id, metric, active, range])
  return data
}

export default function ServerDetail() {
  const { id = '' } = useParams()
  const [server, setServer] = useState<ServerDetailT>()
  const [tab, setTab] = useState<Tab>('Overview')
  const [range, setRange] = useState<TimeRange>('1h')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    fetchServer(id).then(setServer).catch(() => setError('Failed to load server.'))
  }, [id])
  useEffect(() => { load() }, [load])

  // One range drives every chart on the page (matches the device telemetry side).
  const cpuHist = useHistory(id, 'cpu', tab === 'CPU' || tab === 'Overview', range)
  const memHist = useHistory(id, 'memory', tab === 'Memory', range)
  const diskHist = useHistory(id, 'disk', tab === 'Disk', range)
  const netHist = useHistory(id, 'network', tab === 'Network', range)

  if (error) return <div className="p-6 text-red-600">{error}</div>
  if (!server) return <div className="p-6 text-gray-500">Loading…</div>

  const dm = server.detail_metrics
  const m = server.latest_metrics
  const online = server.status === 'active' && !!server.last_seen &&
    Date.now() - new Date(server.last_seen).getTime() < 5 * 60 * 1000

  return (
    <div className="p-6">
      <Link to="/servers" className="text-sm text-blue-600 dark:text-blue-400">← Servers</Link>

      {/* Header */}
      <div className="mt-2 mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{server.hostname}</h1>
          <div className="text-sm text-gray-500 dark:text-gray-400 mt-1 flex flex-wrap gap-x-4">
            <span>{server.os_version || server.os || 'Unknown OS'}</span>
            <span>Arch: {server.arch || '—'}</span>
            <span>Agent v{server.agent_version || '—'}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 text-sm">
          <span className={`px-2 py-0.5 rounded-full text-xs ${online ? 'text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/40' : 'text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/40'}`}>
            {online ? '✅ Online' : '🔴 Offline'}
          </span>
          <div className="text-gray-500 dark:text-gray-400">Last seen: {timeAgo(server.last_seen)}</div>
          {/* One range controls every chart on the page. */}
          <TimeRangeSelector value={range} onChange={setRange} />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b dark:border-gray-700 mb-5">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === t ? 'border-blue-600 text-blue-600 dark:text-blue-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}`}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Overview' && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MetricCard label="CPU" pct={dm.cpu_pct ?? m.cpu_pct} />
            <MetricCard label="Memory" pct={(dm.memory.usage_pct as number) ?? m.memory_pct} />
            <MetricCard label="Disk" pct={m.disk_max_pct} sub={m.disk_max_mount ?? undefined} />
            <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
              <div className="text-xs text-gray-500 dark:text-gray-400">Load (1m)</div>
              <div className="text-2xl font-bold text-gray-900 dark:text-gray-100 mt-1">{dm.load.load1?.toFixed(2) ?? m.load_1?.toFixed(2) ?? '—'}</div>
              <div className="text-xs text-gray-400 mt-2">5m {dm.load.load5?.toFixed(2) ?? '—'} · 15m {dm.load.load15?.toFixed(2) ?? '—'}</div>
            </div>
          </div>
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">CPU — {RANGE_LABEL[range]}</div>
            <LineChart history={cpuHist} fields={['usage_pct']} height={200} />
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <InfoPanel server={server} />
            <AlertsPanel server={server} />
          </div>
        </div>
      )}

      {tab === 'CPU' && (
        <div className="space-y-5">
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">CPU usage (user / system / iowait)</div>
            <LineChart history={cpuHist} fields={['usage_pct', 'user', 'system', 'iowait']} />
          </div>
          {!!dm.cpu_cores.length && (
            <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">Per-core</div>
              <div className="space-y-2">
                {dm.cpu_cores.map((c) => (
                  <div key={c.core} className="flex items-center gap-3">
                    <span className="text-xs w-16 text-gray-500">{c.core}</span>
                    <div className="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
                      <div className={`h-full ${color(c.usage_pct)}`} style={{ width: `${Math.min(100, c.usage_pct)}%` }} />
                    </div>
                    <span className="text-xs w-10 text-right tabular-nums">{Math.round(c.usage_pct)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'Memory' && (
        <div className="space-y-5">
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Memory usage — {RANGE_LABEL[range]}</div>
            <LineChart history={memHist} fields={['usage_pct']} />
          </div>
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            {[['Total', dm.memory.total_bytes], ['Used', dm.memory.used_bytes], ['Cached', dm.memory.cached_bytes], ['Free', dm.memory.free_bytes],
              ['Swap total', dm.memory.swap_total], ['Swap used', dm.memory.swap_used]].map(([l, v]) => (
              <div key={l as string}><div className="text-xs text-gray-500">{l}</div><div className="font-medium text-gray-900 dark:text-gray-100">{fmtBytes(v as number)}</div></div>
            ))}
          </div>
        </div>
      )}

      {tab === 'Disk' && (
        <div className="space-y-5">
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-gray-500"><tr>{['Mount', 'Total', 'Used', 'Free', 'Use%'].map((h) => <th key={h} className="px-2 py-1">{h}</th>)}</tr></thead>
              <tbody>
                {dm.disks.length ? dm.disks.map((d) => (
                  <tr key={d.mount} className="border-t dark:border-gray-700/60">
                    <td className="px-2 py-1 font-medium">{d.mount}</td>
                    <td className="px-2 py-1">{fmtBytes(d.total_bytes)}</td>
                    <td className="px-2 py-1">{fmtBytes(d.used_bytes)}</td>
                    <td className="px-2 py-1">{fmtBytes(d.free_bytes)}</td>
                    <td className={`px-2 py-1 ${(d.usage_pct ?? 0) >= 80 ? 'text-red-600 dark:text-red-400 font-medium' : ''}`}>{d.usage_pct == null ? '—' : `${Math.round(d.usage_pct)}%`}</td>
                  </tr>
                )) : <tr><td colSpan={5} className="px-2 py-6 text-center text-gray-500">No disk data.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Disk utilization — {RANGE_LABEL[range]}</div>
            <LineChart history={diskHist} fields={['usage_pct']} />
          </div>
        </div>
      )}

      {tab === 'Network' && (
        <div className="space-y-5">
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-gray-500"><tr>{['Interface', 'RX bps', 'TX bps', 'RX err', 'TX err'].map((h) => <th key={h} className="px-2 py-1">{h}</th>)}</tr></thead>
              <tbody>
                {dm.interfaces.length ? dm.interfaces.map((i) => (
                  <tr key={i.interface} className="border-t dark:border-gray-700/60">
                    <td className="px-2 py-1 font-medium">{i.interface}</td>
                    <td className="px-2 py-1">{fmtBytes(i.rx_bps)}/s</td>
                    <td className="px-2 py-1">{fmtBytes(i.tx_bps)}/s</td>
                    <td className="px-2 py-1">{i.rx_errors ?? 0}</td>
                    <td className="px-2 py-1">{i.tx_errors ?? 0}</td>
                  </tr>
                )) : <tr><td colSpan={5} className="px-2 py-6 text-center text-gray-500">No interface data.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Throughput — {RANGE_LABEL[range]}</div>
            <LineChart history={netHist} fields={['rx_bps', 'tx_bps']} />
          </div>
        </div>
      )}

      {tab === 'Processes' && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-gray-500">
          Process monitoring is optional — enable <code>collection.processes</code> in the agent config to populate this tab.
        </div>
      )}

      {tab === 'Services' && <ServicesTab server={server} />}
      {tab === 'Roles' && <RolesTab id={id} os={server.os} />}

      {tab === 'Logs' && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-gray-500">
          Server logs are indexed centrally. <Link to={`/logs?q=${encodeURIComponent(server.hostname)}`} className="text-blue-600 dark:text-blue-400">Open Logs filtered by {server.hostname} →</Link>
        </div>
      )}

      {tab === 'Alerts' && <div className="max-w-2xl"><AlertsPanel server={server} /></div>}
    </div>
  )
}

function InfoPanel({ server }: { server: ServerDetailT }) {
  const rows: [string, string][] = [
    ['Hostname', server.hostname], ['OS', server.os_version || server.os || '—'],
    ['Arch', server.arch || '—'], ['Site', server.site?.name ?? '—'],
    ['Agent ID', server.id], ['Agent version', server.agent_version || '—'],
    ['Cert expires', server.cert_expires_at ? new Date(server.cert_expires_at).toLocaleDateString() : '—'],
    ['Collection interval', `${server.collection_interval}s`],
  ]
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">System Information</div>
      <dl className="text-sm space-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4">
            <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
            <dd className="text-gray-900 dark:text-gray-100 font-medium truncate max-w-[60%]" title={v}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

const SEV: Record<string, string> = { critical: 'text-red-600', high: 'text-orange-600', medium: 'text-amber-600', low: 'text-blue-600', info: 'text-gray-500' }
function AlertsPanel({ server }: { server: ServerDetailT }) {
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">Recent Alerts</div>
      {server.recent_alerts.length ? (
        <ul className="text-sm space-y-2">
          {server.recent_alerts.map((a) => (
            <li key={a.id} className="flex items-start justify-between gap-3">
              <div>
                <span className={`font-medium ${SEV[a.severity] ?? ''}`}>{a.name}</span>
                {a.summary && <div className="text-xs text-gray-500">{a.summary}</div>}
              </div>
              <span className="text-xs text-gray-400 whitespace-nowrap">{a.state} · {timeAgo(a.created_at)}</span>
            </li>
          ))}
        </ul>
      ) : <div className="text-sm text-gray-500">No recent alerts.</div>}
    </div>
  )
}

function ServicesTab({ server }: { server: ServerDetailT }) {
  // Services come from the agent's role-check results (per-role). The Roles tab
  // shows them grouped by role; here we present them flat for a quick scan.
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-gray-500">
      Monitored services appear once roles are assigned and the agent reports
      role checks. See the <span className="font-medium">Roles</span> tab to assign roles
      ({server.roles.length ? server.roles.join(', ') : 'none assigned yet'}).
    </div>
  )
}

function RolesTab({ id, os }: { id: string; os: string }) {
  const [assigned, setAssigned] = useState<AssignedRole[]>([])
  const [detected, setDetected] = useState<DetectedRole[]>([])
  const [allRoles, setAllRoles] = useState<ServerRole[]>([])
  const [showAssign, setShowAssign] = useState(false)
  const [pick, setPick] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<AssignedRole | null>(null)

  const load = useCallback(() => {
    fetchServerRoleAssignments(id).then(setAssigned).catch(() => {})
  }, [id])
  useEffect(() => { load(); fetchServerRoles().then(setAllRoles).catch(() => {}) }, [load])

  const assign = async (roleId: number) => {
    setBusy(true)
    try {
      const a = await assignServerRole(id, roleId)
      setNotice(a)
      load()
      setDetected((d) => d.filter((x) => x.role_id !== roleId))
    } finally { setBusy(false); setShowAssign(false); setPick('') }
  }
  const remove = async (roleId: number) => { await removeServerRole(id, roleId); load() }
  const detect = async () => { setDetected(await detectServerRoles(id)) }

  const pickable = useMemo(() => allRoles.filter((r) => !assigned.some((a) => a.role_id === r.id)), [allRoles, assigned])

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <button onClick={() => setShowAssign((v) => !v)} className="px-3 py-2 text-sm bg-blue-600 text-white rounded-lg">+ Assign Role</button>
        <button onClick={detect} className="px-3 py-2 text-sm border rounded-lg dark:border-gray-600 dark:text-gray-300">🔍 Auto-detect</button>
        <span className="text-xs text-gray-400">OS: {os || 'unknown'}</span>
      </div>

      {notice && (
        <div role="alert" className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-xl p-4 text-sm text-green-900 dark:text-green-100">
          <div className="flex items-start justify-between gap-3">
            <div className="font-semibold">✅ Role “{notice.name}” assigned successfully!</div>
            <button onClick={() => setNotice(null)} className="text-green-700 dark:text-green-300 hover:underline text-xs shrink-0">Dismiss</button>
          </div>
          <p className="mt-2">
            The agent picks this up <span className="font-medium">automatically on its next check-in</span> — the
            server pushes the assigned roles back in the metrics response, so no config edit is required as long as
            the agent is reporting metrics.
          </p>
          <p className="mt-2">To apply immediately, restart the agent on the host:</p>
          <pre className="mt-1 bg-green-100 dark:bg-green-950/50 rounded-md px-3 py-2 overflow-x-auto font-mono text-xs">sudo systemctl restart netpulse-agent</pre>
          <p className="mt-2">
            Prefer to manage roles in the config file? Edit
            <code className="mx-1 px-1 rounded bg-green-100 dark:bg-green-950/50">/etc/netpulse-agent/config.json</code>
            and restart:
          </p>
          <pre className="mt-1 bg-green-100 dark:bg-green-950/50 rounded-md px-3 py-2 overflow-x-auto font-mono text-xs">{`"role_checks": {
  "enabled": true,
  "roles": ["${notice.role_type}"]
}`}</pre>
        </div>
      )}

      {showAssign && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4 flex items-end gap-3">
          <label className="text-sm flex-1">
            <span className="text-gray-700 dark:text-gray-300">Role</span>
            <select value={pick} onChange={(e) => setPick(Number(e.target.value))}
              className="mt-1 w-full px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600">
              <option value="">Select a role…</option>
              {pickable.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </label>
          <button disabled={!pick || busy} onClick={() => pick && assign(pick)}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50">Assign</button>
        </div>
      )}

      {!!detected.length && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">🔍 Detected Roles</div>
          <div className="space-y-2">
            {detected.map((d) => (
              <div key={d.role_id} className="flex items-center justify-between text-sm">
                <div>
                  <span className="font-medium">{d.role_name}</span>
                  <span className="text-gray-500"> — {d.matched_services.join(', ')} running ({Math.round(d.confidence * 100)}%)</span>
                </div>
                {d.assigned
                  ? <span className="text-xs text-green-600">assigned</span>
                  : <button onClick={() => assign(d.role_id)} className="px-2 py-1 text-xs border rounded dark:border-gray-600">Assign</button>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {assigned.map((a) => {
          const st = a.status
          const pass = st ? `${st.checks_passed}/${st.checks_total}` : '—'
          const allOk = st && st.checks_total > 0 && st.checks_passed === st.checks_total
          return (
            <div key={a.id} className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
              <div className="flex items-start justify-between">
                <div className="font-semibold text-gray-900 dark:text-gray-100">{a.name}</div>
                {a.auto_detected && <span className="text-[10px] text-gray-400 uppercase">auto</span>}
              </div>
              <div className={`text-sm mt-1 ${allOk ? 'text-green-600' : st && st.checks_total ? 'text-amber-600' : 'text-gray-400'}`}>
                {st && st.checks_total ? `${allOk ? '✅' : '⚠️'} ${pass} pass` : 'No checks reported yet'}
              </div>
              <button onClick={() => remove(a.role_id)} className="mt-3 text-xs text-red-600 hover:underline">Remove</button>
            </div>
          )
        })}
        {!assigned.length && <div className="text-sm text-gray-500 col-span-full">No roles assigned. Use “Assign Role” or “Auto-detect”.</div>}
      </div>
    </div>
  )
}

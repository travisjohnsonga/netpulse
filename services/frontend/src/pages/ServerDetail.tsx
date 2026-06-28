import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import TimeRangeSelector, { RANGE_LABEL, type TimeRange } from '../components/TimeRangeSelector'
import {
  fetchServer, fetchServerMetricHistory, fetchServerRoleAssignments,
  assignServerRole, removeServerRole, detectServerRoles, fetchServerRoles,
  changeServerSite, fetchSites, fetchServerConfig, updateServerConfig, updateServerLiveness,
  type ServerDetail as ServerDetailT, type MetricHistory,
  type AssignedRole, type DetectedRole, type ServerRole, type Site,
  type AgentDesiredConfig,
} from '../api/client'
import { useCapabilities } from '../store/authStore'
import { parseApiErrors } from '../api/errors'

const TABS = ['Overview', 'CPU', 'Memory', 'Disk', 'Network', 'Processes', 'Services', 'Roles', 'Config', 'Logs', 'Alerts'] as const
type Tab = typeof TABS[number]

function fmtBytes(n?: number | null): string {
  if (n == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}
// Display a version with exactly one leading "v" for numeric versions
// (git describe yields "v1.0.0", so "Agent v{version}" would double it to
// "vv1.0.0"). Non-numeric versions like "dev" are shown as-is.
function fmtVersion(v?: string | null): string {
  if (!v) return '—'
  const bare = v.replace(/^v+/, '')
  return /^\d/.test(bare) ? `v${bare}` : v
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

// Build an ECharts [ms, value|null][] series that BREAKS across data gaps. The
// backend returns sparse points (aggregateWindow createEmpty:false → no points
// while a host is down), so we detect time gaps and insert a null so the line
// breaks instead of drawing a (possibly overshooting) spline across the gap.
// A gap = consecutive samples more than gapFactor× the typical interval apart
// (the typical interval = the median delta, robust to the gap itself).
export function withGaps(
  times: number[],
  valueAt: (t: number) => number | null,
  gapFactor = 2.5,
): [number, number | null][] {
  if (!times.length) return []
  const deltas: number[] = []
  for (let i = 1; i < times.length; i++) deltas.push(times[i] - times[i - 1])
  const sorted = [...deltas].sort((a, b) => a - b)
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0
  const threshold = median > 0 ? median * gapFactor : Infinity
  const out: [number, number | null][] = []
  for (let i = 0; i < times.length; i++) {
    if (i > 0 && times[i] - times[i - 1] > threshold) {
      out.push([times[i - 1] + median, null]) // null in the gap → line breaks here
    }
    out.push([times[i], valueAt(times[i])])
  }
  return out
}

function LineChart({ history, fields, height = 260 }: { history?: MetricHistory; fields: string[]; height?: number }) {
  const series = history?.series ?? []
  if (!series.length) return <div className="text-sm text-gray-500 py-10 text-center">No time-series data yet.</div>
  const byTime = new Map<number, Record<string, unknown>>()
  for (const r of series) byTime.set(new Date(r.t as string).getTime(), r)
  const times = [...byTime.keys()].sort((a, b) => a - b)
  const option: EChartsOption = {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: '#9ca3af' } },
    grid: { left: 48, right: 16, top: 16, bottom: 36 },
    // Time axis so a gap is spatially honest (downtime = a visible empty span,
    // not evenly-spaced categories that hide the gap's duration).
    xAxis: {
      type: 'time',
      axisLabel: { color: '#9ca3af', formatter: (v: number) => new Date(v).toLocaleTimeString() },
    },
    // Server metrics (cpu/mem/disk %, load, network bps) are never negative; min:0
    // makes a sub-zero artifact impossible even if a future smooth is re-enabled.
    yAxis: { type: 'value', min: 0, axisLabel: { color: '#9ca3af' } },
    series: fields.map((f) => ({
      // smooth:false → straight segments (no spline overshoot, honest for
      // monitoring data); connectNulls:false → the inserted gap nulls break the line.
      name: f, type: 'line', smooth: false, showSymbol: false, connectNulls: false,
      areaStyle: { opacity: 0.08 },
      data: withGaps(times, (t) => {
        const v = byTime.get(t)?.[f]
        return typeof v === 'number' ? v : null
      }),
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
  // Prefer the server's authoritative is_online (same threshold the liveness
  // alert uses); fall back to a client-side window for older API responses.
  const online = typeof server.is_online === 'boolean'
    ? server.is_online
    : (server.status === 'active' && !!server.last_seen &&
       Date.now() - new Date(server.last_seen).getTime() < 5 * 60 * 1000)

  return (
    <div className="p-6">
      <Link to="/servers" className="text-sm text-blue-600 dark:text-blue-400">← Servers</Link>

      {/* Header */}
      <div className="mt-2 mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{server.hostname}</h1>
          <div className="text-sm text-gray-500 dark:text-gray-400 mt-1 flex flex-wrap gap-x-4">
            <span>{server.os_name || server.os || 'Unknown OS'}</span>
            <span>Arch: {server.arch || '—'}</span>
            <span>Agent {fmtVersion(server.agent_version)}</span>
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
            <InfoPanel server={server} onChanged={load} />
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

      {tab === 'Services' && <ServicesTab server={server} onTab={setTab} onChanged={load} />}
      {tab === 'Roles' && <RolesTab id={id} os={server.os} />}
      {tab === 'Config' && <ConfigTab id={id} os={server.os} />}

      {tab === 'Logs' && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-gray-500">
          Server logs are indexed centrally. <Link to={`/logs?q=${encodeURIComponent(server.hostname)}`} className="text-blue-600 dark:text-blue-400">Open Logs filtered by {server.hostname} →</Link>
        </div>
      )}

      {tab === 'Alerts' && <div className="max-w-2xl"><AlertsPanel server={server} /></div>}
    </div>
  )
}

function InfoPanel({ server, onChanged }: { server: ServerDetailT; onChanged: () => void }) {
  // Hostname is self-reported by the agent at enrollment (and re-asserted on every
  // re-enrollment), so it's read-only here — editing it would just be overwritten.
  const rows: [string, string][] = [
    // OS shows the detected distro/product (os_name); falls back to the os_family
    // for agents predating OS-detail. Version + Kernel only shown when reported.
    ['OS', server.os_name || server.os || '—'],
    ...(server.os_version ? [['OS version', server.os_version] as [string, string]] : []),
    ...(server.os_kernel ? [['Kernel', server.os_kernel] as [string, string]] : []),
    ['Arch', server.arch || '—'],
    ['Agent ID', server.id], ['Agent version', fmtVersion(server.agent_version)],
    ['Cert expires', server.cert_expires_at ? new Date(server.cert_expires_at).toLocaleDateString() : '—'],
    ['Collection interval', `${server.collection_interval}s`],
  ]
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">System Information</div>
      <dl className="text-sm space-y-1.5">
        <div className="flex justify-between gap-4">
          <dt className="text-gray-500 dark:text-gray-400">Hostname <span className="text-[10px] text-gray-400">(reported by agent)</span></dt>
          <dd className="text-gray-900 dark:text-gray-100 font-medium truncate max-w-[60%]" title={server.hostname}>{server.hostname}</dd>
        </div>
        <SiteRow server={server} onChanged={onChanged} />
        <LivenessRow server={server} onChanged={onChanged} />
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

// Liveness-alert row: shows whether the offline alert is enabled + the effective
// threshold, with a capability-gated enable/disable toggle (agent:edit). Disable
// is the escape hatch for a host that legitimately sleeps (the lab) so it doesn't
// alert-storm. Audit-logged server-side.
function LivenessRow({ server, onChanged }: { server: ServerDetailT; onChanged: () => void }) {
  const caps = useCapabilities()
  const canEdit = caps.includes('agent:edit')
  const [busy, setBusy] = useState(false)
  const enabled = server.liveness_alerts_enabled !== false
  const thr = server.offline_threshold_seconds ? `${server.offline_threshold_seconds}s` : 'default'

  const toggle = async () => {
    setBusy(true)
    try {
      await updateServerLiveness(server.id, { liveness_alerts_enabled: !enabled })
      onChanged()
    } catch { /* surfaced elsewhere */ } finally { setBusy(false) }
  }

  return (
    <div className="flex justify-between gap-4 items-center">
      <dt className="text-gray-500 dark:text-gray-400">Offline alert</dt>
      <dd className="text-gray-900 dark:text-gray-100 font-medium flex items-center gap-2">
        <span className={enabled ? '' : 'text-gray-400'}>
          {enabled ? `Enabled · offline > ${thr}` : 'Disabled'}
        </span>
        {canEdit && (
          <button onClick={toggle} disabled={busy}
            className="text-xs px-2 py-0.5 border rounded dark:border-gray-600 text-gray-600 dark:text-gray-300 disabled:opacity-50">
            {enabled ? 'Disable' : 'Enable'}
          </button>
        )}
      </dd>
    </div>
  )
}

// Site row with an inline, capability-gated reassignment control (agent:edit).
// The site lives on the linked device; the change is audit-logged server-side.
function SiteRow({ server, onChanged }: { server: ServerDetailT; onChanged: () => void }) {
  const caps = useCapabilities()
  const canEdit = caps.includes('agent:edit')
  const [editing, setEditing] = useState(false)
  const [sites, setSites] = useState<Site[]>([])
  const [pick, setPick] = useState<number | ''>(server.site?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (editing && sites.length === 0) fetchSites().then(setSites).catch(() => {})
  }, [editing, sites.length])

  const save = async () => {
    setBusy(true); setError(null)
    try {
      await changeServerSite(server.id, pick === '' ? null : pick)
      setEditing(false)
      onChanged()
    } catch (e) {
      // Surface the ACTUAL server error (e.g. the response `detail`) instead of a
      // generic message, so the operator sees the real reason.
      setError(parseApiErrors(e, 'Failed to change site.'))
    } finally { setBusy(false) }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-1.5 py-1">
        <div className="flex items-center gap-2">
          <span className="text-gray-500 dark:text-gray-400">Site</span>
          <select value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))}
            className="flex-1 px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-600">
            <option value="">Unassigned</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <button onClick={save} disabled={busy} className="px-2 py-1 text-xs bg-blue-600 text-white rounded disabled:opacity-50">Save</button>
          <button onClick={() => { setEditing(false); setPick(server.site?.id ?? '') }} className="px-2 py-1 text-xs border rounded dark:border-gray-600 dark:text-gray-300">Cancel</button>
        </div>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
    )
  }
  return (
    <div className="flex justify-between gap-4 items-center">
      <dt className="text-gray-500 dark:text-gray-400">Site</dt>
      <dd className="text-gray-900 dark:text-gray-100 font-medium truncate max-w-[60%] flex items-center gap-2">
        <span title={server.site?.name ?? ''}>{server.site?.name ?? '—'}</span>
        {canEdit && (
          <button onClick={() => setEditing(true)} className="text-xs text-blue-600 dark:text-blue-400 hover:underline">Change</button>
        )}
      </dd>
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

function ServicesTab({ server, onTab, onChanged }: { server: ServerDetailT; onTab: (t: Tab) => void; onChanged: () => void }) {
  // Two distinct sections: WATCHED services (stability — health + alerts) on top,
  // and the GENERAL running-services list (the 'services' toggle's data,
  // Agent.reported_services — "what's running" visibility) below.
  // (Role-specific service/port CHECKS live on the Roles tab.)
  const [q, setQ] = useState('')
  const services = server.reported_services ?? []
  const collected = server.services_collected === true
  const filtered = q
    ? services.filter((s) => s.name.toLowerCase().includes(q.toLowerCase()))
    : services

  const LinkBtn = ({ to, label }: { to: Tab; label: string }) => (
    <button onClick={() => onTab(to)} className="text-blue-600 dark:text-blue-400 hover:underline font-medium">{label}</button>
  )

  let body
  if (!collected) {
    // (a) toggle OFF
    body = (
      <div className="text-sm text-gray-500 dark:text-gray-400">
        The <span className="font-medium">Services</span> collection toggle is off, so this host
        isn't reporting its running services. Enable it on the{' '}
        <LinkBtn to="Config" label="Config" /> tab to list running services here.
      </div>
    )
  } else if (services.length === 0) {
    // (b) toggle ON, no data yet
    body = (
      <div className="text-sm text-gray-500 dark:text-gray-400">
        Services collection is on — waiting for the agent's next check-in.
      </div>
    )
  } else {
    body = (
      <>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="text-sm font-medium text-gray-700 dark:text-gray-300">
            {services.length} running service{services.length === 1 ? '' : 's'}
          </div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter services…"
            className="px-3 py-1.5 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600 w-48" />
        </div>
        {filtered.length === 0 ? (
          <div className="text-sm text-gray-400">No services match “{q}”.</div>
        ) : (
          <ul className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-sm">
            {filtered.map((s) => (
              <li key={s.name} className="flex items-center gap-2 text-gray-700 dark:text-gray-300"
                  title={`${s.name}${s.state ? ` · ${s.state}` : ''}${s.start_type ? ` · ${s.start_type}` : ''}`}>
                <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${s.running ? 'bg-green-500' : 'bg-gray-400'}`} />
                <span className="truncate">{s.name}</span>
                {s.state && <span className="text-xs text-gray-400 shrink-0">{s.state}</span>}
              </li>
            ))}
          </ul>
        )}
        {q && filtered.length > 0 && (
          <div className="mt-2 text-xs text-gray-400">{filtered.length} of {services.length} shown</div>
        )}
      </>
    )
  }

  return (
    <div className="space-y-5">
      <WatchedServicesSection server={server} onChanged={onChanged} />
      <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6">
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">All running services</div>
        {body}
        {/* (c) always present: role-specific CHECKS live on the Roles tab. */}
        <p className="mt-4 text-xs text-gray-400">
          Looking for role-specific service &amp; port <em>checks</em> (pass/fail)? Those are on the{' '}
          <LinkBtn to="Roles" label="Roles" /> tab.
        </p>
      </div>
    </div>
  )
}

// Stability monitoring: operator-chosen watched services + their health (up/down,
// last change, restarts/24h) + add/remove. Role-independent; alerts fire on
// down/flap server-side. Edits write desired_config.stability.services (applied
// on the agent's next check-in), gated by agent:edit.
function WatchedServicesSection({ server, onChanged }: { server: ServerDetailT; onChanged: () => void }) {
  const canEdit = useCapabilities().includes('agent:edit')
  const ws = server.watched_services
  const configured = ws?.configured ?? []
  const byName = new Map((ws?.statuses ?? []).map((s) => [s.name, s]))
  const [add, setAdd] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const save = async (services: string[]) => {
    setBusy(true); setErr(null)
    try {
      await updateServerConfig(server.id, { stability: { services } })
      setAdd('')
      onChanged()
    } catch (e) { setErr(parseApiErrors(e, 'Failed to update watched services.')) }
    finally { setBusy(false) }
  }
  const addOne = () => {
    const name = add.trim()
    if (name && !configured.includes(name)) save([...configured, name])
  }
  const removeOne = (name: string) => save(configured.filter((s) => s !== name))

  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6">
      <div className="flex items-center justify-between mb-1">
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Watched services</div>
        <span className="text-xs text-gray-400">stability — alerts on down/restart</span>
      </div>
      {err && <div className="mb-2 text-xs text-red-600">{err}</div>}

      {configured.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
          No services watched yet. Add one (e.g. <code>docker</code>, <code>sshd</code>) to alert
          when it stops or restarts repeatedly — no role required.
        </p>
      ) : (
        <ul className="divide-y dark:divide-gray-700 mb-3">
          {configured.map((name) => {
            const st = byName.get(name)
            const up = st?.running === true
            const pending = !st || !st.collected_at
            return (
              <li key={name} className="flex items-center gap-3 py-2 text-sm">
                <span className={`h-2 w-2 rounded-full shrink-0 ${pending ? 'bg-gray-300 dark:bg-gray-600' : up ? 'bg-green-500' : 'bg-red-500'}`} />
                <span className="font-medium text-gray-900 dark:text-gray-100 w-40 truncate" title={name}>{name}</span>
                <span className={`text-xs ${pending ? 'text-gray-400' : up ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                  {pending ? 'pending check-in' : up ? 'up' : `down${st?.down_since ? ` since ${timeAgo(st.down_since)}` : ''}`}
                </span>
                {st?.state && !pending && <span className="text-xs text-gray-400">({st.state})</span>}
                {!!st?.restarts_24h && <span className="text-xs text-amber-600 dark:text-amber-400">↻ {st.restarts_24h} restart{st.restarts_24h === 1 ? '' : 's'}/24h</span>}
                {st?.last_change_at && !pending && <span className="text-xs text-gray-400 ml-auto">changed {timeAgo(st.last_change_at)}</span>}
                {canEdit && (
                  <button onClick={() => removeOne(name)} disabled={busy}
                    className={`text-xs text-gray-400 hover:text-red-600 ${st?.last_change_at && !pending ? 'ml-2' : 'ml-auto'} disabled:opacity-50`}
                    title="Stop watching">✗</button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {canEdit ? (
        <div className="flex items-center gap-2">
          <input value={add} onChange={(e) => setAdd(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addOne() }}
            placeholder="service name (e.g. docker)"
            className="px-3 py-1.5 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600 flex-1 max-w-xs" />
          <button onClick={addOne} disabled={busy || !add.trim()}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50">Watch</button>
          <span className="text-xs text-gray-400">applies on next check-in (~30s)</span>
        </div>
      ) : (
        <p className="text-xs text-gray-400">Requires the agent:edit capability to change.</p>
      )}
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
        {assigned.map((a) => <RoleCard key={a.id} a={a} onRemove={remove} />)}
        {!assigned.length && <div className="text-sm text-gray-500 col-span-full">No roles assigned. Use “Assign Role” or “Auto-detect”.</div>}
      </div>
    </div>
  )
}

// One role card: the X/Y-pass summary header + an expandable per-check breakdown
// (each service running/not, each port listening/not, each custom check) driven
// by the role's own ServerRole definitions — shared frame, role-specific checks.
function RoleCard({ a, onRemove }: { a: AssignedRole; onRemove: (roleId: number) => void }) {
  const [open, setOpen] = useState(false)
  const st = a.status
  const pass = st ? `${st.checks_passed}/${st.checks_total}` : '—'
  const allOk = st && st.checks_total > 0 && st.checks_passed === st.checks_total
  const hasChecks = !!st && (st.services.length + st.ports.length + (st.custom?.length ?? 0)) > 0

  const Check = ({ ok, label, sub }: { ok: boolean; label: string; sub?: string }) => (
    <li className="flex items-center gap-2 py-0.5">
      <span className={ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>{ok ? '✓' : '✗'}</span>
      <span className="text-gray-800 dark:text-gray-200">{label}</span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </li>
  )

  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="flex items-start justify-between">
        <div className="font-semibold text-gray-900 dark:text-gray-100">{a.name}</div>
        {a.auto_detected && <span className="text-[10px] text-gray-400 uppercase">auto</span>}
      </div>
      <button
        onClick={() => hasChecks && setOpen((v) => !v)}
        className={`text-sm mt-1 flex items-center gap-1 ${allOk ? 'text-green-600' : st && st.checks_total ? 'text-amber-600' : 'text-gray-400'} ${hasChecks ? 'hover:underline' : 'cursor-default'}`}>
        {st && st.checks_total ? `${allOk ? '✅' : '⚠️'} ${pass} pass` : 'No checks reported yet'}
        {hasChecks && <span className="text-xs text-gray-400">{open ? '▾' : '▸'}</span>}
      </button>

      {open && st && (
        <div className="mt-2 border-t dark:border-gray-700 pt-2 space-y-2 text-sm">
          {st.services.length > 0 && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-0.5">Services</div>
              <ul>{st.services.map((s) => (
                <Check key={s.name} ok={!!s.running} label={s.name}
                  sub={s.running ? (s.state || 'running') : (s.state || 'not running')} />
              ))}</ul>
            </div>
          )}
          {st.ports.length > 0 && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-0.5">Ports</div>
              <ul>{st.ports.map((p) => (
                <Check key={`${p.port}/${p.proto}`} ok={!!p.open}
                  label={`${p.name ? `${p.name} ` : ''}${p.port}/${p.proto}`}
                  sub={p.open ? 'listening' : 'closed'} />
              ))}</ul>
            </div>
          )}
          {!!st.custom?.length && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-0.5">Custom</div>
              <ul>{st.custom.map((c, i) => (
                <Check key={c.name ?? i} ok={!!(c.passed ?? c.ok)} label={c.name ?? `check ${i + 1}`} />
              ))}</ul>
            </div>
          )}
          {st.collected_at && <div className="text-xs text-gray-400">Last checked {timeAgo(st.collected_at)}</div>}
        </div>
      )}

      <button onClick={() => onRemove(a.role_id)} className="mt-3 text-xs text-red-600 hover:underline">Remove</button>
    </div>
  )
}

// Collection-toggle labels (keys come from the server's effective config, which
// covers the server-managed toggles — not "processes", which isn't pull-managed).
const COLLECTION_LABELS: Record<string, string> = {
  cpu: 'CPU', memory: 'Memory', disk: 'Disk', network: 'Network', services: 'Services',
}

function ConfigTab({ id, os }: { id: string; os: string }) {
  const canEdit = useCapabilities().includes('agent:edit')
  const [cfg, setCfg] = useState<AgentDesiredConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // 'idle' | 'pending' (saved, awaiting agent check-in) | 'applied'
  const [saveState, setSaveState] = useState<'idle' | 'pending' | 'applied'>('idle')
  const [newMount, setNewMount] = useState('')
  const [mountList, setMountList] = useState<'exclude_mounts' | 'include_mounts'>('exclude_mounts')

  const load = useCallback(() => {
    fetchServerConfig(id).then(setCfg).catch(() => setError('Failed to load config.'))
  }, [id])
  useEffect(() => { load() }, [load])

  // After a save, poll the server's last_seen; once it advances past the save
  // time the agent has checked in and pulled the change → "applied".
  useEffect(() => {
    if (saveState !== 'pending') return
    const savedAt = Date.now()
    const t = setInterval(async () => {
      try {
        const s = await fetchServer(id)
        if (s.last_seen && new Date(s.last_seen).getTime() > savedAt) {
          setSaveState('applied')
          clearInterval(t)
        }
      } catch { /* keep polling */ }
    }, 8000)
    return () => clearInterval(t)
  }, [saveState, id])

  if (error) return <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-red-600">{error}</div>
  if (!cfg) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  const intervalValid = cfg.interval_seconds >= 10 && cfg.interval_seconds <= 3600

  const save = async () => {
    if (!intervalValid) return
    setSaving(true); setError(null)
    try {
      const updated = await updateServerConfig(id, {
        collection: cfg.collection,
        interval_seconds: cfg.interval_seconds,
        disk: cfg.disk,
      })
      setCfg(updated)
      setSaveState('pending')
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status
      setError(status === 403 ? 'You lack the agent:edit capability to change config.'
        : status === 400 ? 'The server rejected the config (check the interval / values).'
        : 'Failed to save config.')
    } finally { setSaving(false) }
  }

  const setToggle = (k: string, v: boolean) => {
    setCfg({ ...cfg, collection: { ...cfg.collection, [k]: v } }); setSaveState('idle')
  }
  const addMount = () => {
    const m = newMount.trim()
    if (!m) return
    if (!cfg.disk[mountList].includes(m)) {
      setCfg({ ...cfg, disk: { ...cfg.disk, [mountList]: [...cfg.disk[mountList], m] } })
      setSaveState('idle')
    }
    setNewMount('')
  }
  const removeMount = (list: 'exclude_mounts' | 'include_mounts', m: string) => {
    setCfg({ ...cfg, disk: { ...cfg.disk, [list]: cfg.disk[list].filter((x) => x !== m) } })
    setSaveState('idle')
  }

  const mountHint = os === 'windows' ? 'e.g. D: (drive letter)' : 'e.g. /var or /mnt/data'
  const card = 'bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4'

  return (
    <div className="space-y-4 max-w-2xl">
      {!canEdit && (
        <div className="text-xs px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border dark:border-gray-700">
          Read-only — editing the agent config requires the <code>agent:edit</code> capability.
        </div>
      )}
      {saveState !== 'idle' && (
        <div className={`text-sm px-3 py-2 rounded-lg border ${saveState === 'applied'
          ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-300'
          : 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300'}`}>
          {saveState === 'applied'
            ? '✅ Applied — the agent has checked in since your change.'
            : '⏳ Saved — applies on the agent’s next check-in (~30s). Config is pull-based, not instant.'}
        </div>
      )}

      {/* Collection toggles */}
      <div className={card}>
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">Collection</div>
        <div className="space-y-2">
          {Object.keys(cfg.collection).map((k) => (
            <label key={k} className="flex items-center justify-between text-sm">
              <span className="text-gray-700 dark:text-gray-300">{COLLECTION_LABELS[k] ?? k}</span>
              <input type="checkbox" checked={cfg.collection[k]} disabled={!canEdit}
                onChange={(e) => setToggle(k, e.target.checked)} className="h-4 w-4 disabled:opacity-50" />
            </label>
          ))}
        </div>
        <label className="flex items-center justify-between text-sm mt-4">
          <span className="text-gray-700 dark:text-gray-300">Interval (seconds) <span className="text-gray-400">10–3600</span></span>
          <input type="number" min={10} max={3600} value={cfg.interval_seconds} disabled={!canEdit}
            onChange={(e) => { setCfg({ ...cfg, interval_seconds: Number(e.target.value) }); setSaveState('idle') }}
            className={`w-24 px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-600 disabled:opacity-50 ${intervalValid ? '' : 'border-red-500'}`} />
        </label>
        {!intervalValid && <div className="text-xs text-red-600 mt-1 text-right">Interval must be 10–3600 seconds.</div>}
      </div>

      {/* Disk monitoring */}
      <div className={card}>
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-1">Disk monitoring</div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Removable/optical media is skipped automatically. Exclude drops a drive; include (if any) limits to listed drives. {mountHint}.
        </p>
        {(['exclude_mounts', 'include_mounts'] as const).map((list) => (
          <div key={list} className="mb-3">
            <div className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              {list === 'exclude_mounts' ? 'Exclude' : 'Include (empty = all)'}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {cfg.disk[list].length === 0 && <span className="text-xs text-gray-400">none</span>}
              {cfg.disk[list].map((m) => (
                <span key={m} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 font-mono">
                  {m}
                  {canEdit && <button onClick={() => removeMount(list, m)} className="text-red-600 hover:text-red-800">×</button>}
                </span>
              ))}
            </div>
          </div>
        ))}
        {canEdit && (
          <div className="flex items-center gap-2 mt-2">
            <select value={mountList} onChange={(e) => setMountList(e.target.value as 'exclude_mounts' | 'include_mounts')}
              className="px-2 py-1 text-xs border rounded dark:bg-gray-900 dark:border-gray-600">
              <option value="exclude_mounts">Exclude</option>
              <option value="include_mounts">Include</option>
            </select>
            <input value={newMount} onChange={(e) => setNewMount(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addMount()} placeholder={mountHint}
              className="flex-1 px-2 py-1 text-xs border rounded dark:bg-gray-900 dark:border-gray-600 font-mono" />
            <button onClick={addMount} className="px-2 py-1 text-xs border rounded dark:border-gray-600 dark:text-gray-300">Add</button>
          </div>
        )}
      </div>

      {canEdit && (
        <div className="flex justify-end">
          <button onClick={save} disabled={saving || !intervalValid}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50">
            {saving ? 'Saving…' : 'Save config'}
          </button>
        </div>
      )}
    </div>
  )
}

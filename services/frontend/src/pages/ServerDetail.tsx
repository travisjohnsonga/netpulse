import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import TimeRangeSelector, { RANGE_LABEL, type TimeRange } from '../components/TimeRangeSelector'
import {
  fetchServer, fetchServerMetricHistory, fetchServerRoleAssignments,
  assignServerRole, removeServerRole, detectServerRoles, fetchServerRoles,
  changeServerSite, fetchSites, fetchServerConfig, updateServerConfig, updateServerLiveness,
  updateServerAlerting,
  type ServerDetail as ServerDetailT, type MetricHistory, type ServerNetworkState,
  type ServerDetailMetrics,
  type AssignedRole, type DetectedRole, type ServerRole, type Site,
  type AgentDesiredConfig,
} from '../api/client'
import { useCapabilities } from '../store/authStore'
import AlertingControl from '../components/AlertingControl'
import { parseApiErrors } from '../api/errors'
import { STRIPED_ROW, CONTENT_TABLE } from '../lib/tableStyles'
import { useTabParam } from '../lib/useTabParam'
import { fetchPingSummary, fetchLogs, type PingSummary, type LogEntry } from '../api/client'
import PingSparkline, { pingColor } from '../components/PingSparkline'
import { severityBadge } from '../lib/severity'

const TABS = ['Overview', 'Statistics', 'Processes', 'Services', 'Roles', 'Config', 'Logs', 'Alerts'] as const
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

// Collector-originated network reachability chip (complements the Agent chip).
// "not probed" = no routable host IP (synthetic device record) — never a false
// "unreachable" (#133 lesson).
function NetworkChip({ net }: { net?: ServerNetworkState }) {
  if (!net || !net.probed) {
    return (
      <span className="px-2 py-0.5 rounded-full text-xs text-gray-500 bg-gray-100 dark:text-gray-300 dark:bg-gray-700/50"
        title={net?.reason || 'No routable host IP reported by the agent yet'}>
        Network: not probed
      </span>
    )
  }
  if (net.reachable) {
    return (
      <span className="px-2 py-0.5 rounded-full text-xs text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/40"
        title={net.ip ? `Collector reached ${net.ip}` : undefined}>
        Network: reachable{net.rtt_ms != null ? ` ${net.rtt_ms}ms` : ''}
      </span>
    )
  }
  return (
    <span className="px-2 py-0.5 rounded-full text-xs text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/40"
      title={net.ip ? `Collector cannot reach ${net.ip}` : undefined}>
      Network: unreachable
    </span>
  )
}

function MetricCard({ label, pct, sub }: { label: string; pct: number | null; sub?: string }) {
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="text-xs text-gray-500 dark:text-gray-300">{label}</div>
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

const STAT_CARD = 'bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4'
const STAT_TITLE = 'text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2'
const TOP_N = 5

// "Show all N / Show top 5" toggle for the multi-item resource lists.
function ShowAll({ total, open, onToggle }: { total: number; open: boolean; onToggle: () => void }) {
  if (total <= TOP_N) return null
  return (
    <button onClick={onToggle}
      className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400">
      {open ? `Show top ${TOP_N}` : `Show all ${total}`}
    </button>
  )
}

// Combined Statistics tab: CPU + Memory + Disk + Network on one page. The
// multi-item lists (cores / disks / interfaces) show the top 5 by utilization
// with a "Show all" expand. All four histories are loaded by the parent when
// this tab is active; the page range selector drives them.
function StatisticsTab({ dm, range, cpuHist, memHist, diskHist, netHist }: {
  dm: ServerDetailMetrics; range: TimeRange
  cpuHist?: MetricHistory; memHist?: MetricHistory; diskHist?: MetricHistory; netHist?: MetricHistory
}) {
  const [coresOpen, setCoresOpen] = useState(false)
  const [disksOpen, setDisksOpen] = useState(false)
  const [nicsOpen, setNicsOpen] = useState(false)

  const cores = [...dm.cpu_cores].sort((a, b) => b.usage_pct - a.usage_pct)
  const disks = [...dm.disks].sort((a, b) => (b.usage_pct ?? 0) - (a.usage_pct ?? 0))
  const nics = [...dm.interfaces].sort(
    (a, b) => ((b.rx_bps ?? 0) + (b.tx_bps ?? 0)) - ((a.rx_bps ?? 0) + (a.tx_bps ?? 0)))
  const shownCores = coresOpen ? cores : cores.slice(0, TOP_N)
  const shownDisks = disksOpen ? disks : disks.slice(0, TOP_N)
  const shownNics = nicsOpen ? nics : nics.slice(0, TOP_N)

  return (
    <div className="space-y-5">
      <div className="grid lg:grid-cols-2 gap-5">
        {/* CPU */}
        <div className={STAT_CARD}>
          <div className={STAT_TITLE}>CPU — {RANGE_LABEL[range]}</div>
          <LineChart history={cpuHist} fields={['usage_pct', 'user', 'system', 'iowait']} height={200} />
          {!!cores.length && (
            <div className="mt-4">
              <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
                Per-core{cores.length > TOP_N && !coresOpen ? ` (top ${TOP_N} of ${cores.length})` : ''}
              </div>
              <div className="space-y-2">
                {shownCores.map((c) => (
                  <div key={c.core} className="flex items-center gap-3">
                    <span className="text-xs w-16 text-gray-500 dark:text-gray-400">{c.core}</span>
                    <div className="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
                      <div className={`h-full ${color(c.usage_pct)}`} style={{ width: `${Math.min(100, c.usage_pct)}%` }} />
                    </div>
                    <span className="text-xs w-10 text-right tabular-nums text-gray-600 dark:text-gray-300">{Math.round(c.usage_pct)}%</span>
                  </div>
                ))}
              </div>
              <ShowAll total={cores.length} open={coresOpen} onToggle={() => setCoresOpen((v) => !v)} />
            </div>
          )}
        </div>

        {/* Memory */}
        <div className={STAT_CARD}>
          <div className={STAT_TITLE}>Memory — {RANGE_LABEL[range]}</div>
          <LineChart history={memHist} fields={['usage_pct']} height={200} />
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
            {[['Total', dm.memory.total_bytes], ['Used', dm.memory.used_bytes], ['Cached', dm.memory.cached_bytes],
              ['Free', dm.memory.free_bytes], ['Swap total', dm.memory.swap_total], ['Swap used', dm.memory.swap_used]].map(([l, v]) => (
              <div key={l as string}>
                <div className="text-xs text-gray-500 dark:text-gray-400">{l}</div>
                <div className="font-medium text-gray-900 dark:text-gray-100">{fmtBytes(v as number)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Disk */}
      <div className={STAT_CARD}>
        <div className={STAT_TITLE}>Disk{disks.length > TOP_N && !disksOpen ? ` (top ${TOP_N} of ${disks.length} by use%)` : ''}</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-gray-500 dark:text-gray-400"><tr>{['Mount', 'Total', 'Used', 'Free', 'Use%'].map((h) => <th key={h} className="px-2 py-1">{h}</th>)}</tr></thead>
            <tbody>
              {shownDisks.length ? shownDisks.map((d) => (
                <tr key={d.mount} className={STRIPED_ROW}>
                  <td className="px-2 py-1 font-medium text-gray-800 dark:text-gray-200">{d.mount}</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{fmtBytes(d.total_bytes)}</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{fmtBytes(d.used_bytes)}</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{fmtBytes(d.free_bytes)}</td>
                  <td className={`px-2 py-1 ${(d.usage_pct ?? 0) >= 80 ? 'text-red-600 dark:text-red-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`}>{d.usage_pct == null ? '—' : `${Math.round(d.usage_pct)}%`}</td>
                </tr>
              )) : <tr><td colSpan={5} className="px-2 py-6 text-center text-gray-500 dark:text-gray-400">No disk data.</td></tr>}
            </tbody>
          </table>
        </div>
        <ShowAll total={disks.length} open={disksOpen} onToggle={() => setDisksOpen((v) => !v)} />
        <div className="mt-4"><LineChart history={diskHist} fields={['usage_pct']} height={200} /></div>
      </div>

      {/* Network */}
      <div className={STAT_CARD}>
        <div className={STAT_TITLE}>Network{nics.length > TOP_N && !nicsOpen ? ` (top ${TOP_N} of ${nics.length} by throughput)` : ''}</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-gray-500 dark:text-gray-400"><tr>{['Interface', 'RX bps', 'TX bps', 'RX err', 'TX err'].map((h) => <th key={h} className="px-2 py-1">{h}</th>)}</tr></thead>
            <tbody>
              {shownNics.length ? shownNics.map((i) => (
                <tr key={i.interface} className={STRIPED_ROW}>
                  <td className="px-2 py-1 font-medium text-gray-800 dark:text-gray-200">{i.interface}</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{fmtBytes(i.rx_bps)}/s</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{fmtBytes(i.tx_bps)}/s</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{i.rx_errors ?? 0}</td>
                  <td className="px-2 py-1 text-gray-600 dark:text-gray-300">{i.tx_errors ?? 0}</td>
                </tr>
              )) : <tr><td colSpan={5} className="px-2 py-6 text-center text-gray-500 dark:text-gray-400">No interface data.</td></tr>}
            </tbody>
          </table>
        </div>
        <ShowAll total={nics.length} open={nicsOpen} onToggle={() => setNicsOpen((v) => !v)} />
        <div className="mt-4"><LineChart history={netHist} fields={['rx_bps', 'tx_bps']} height={200} /></div>
      </div>
    </div>
  )
}

// Server-scoped logs embedded on the detail Logs tab (same device_hostname
// filter the Logs page uses) — no redirect; recent N for this host inline.
function ServerLogsTab({ hostname }: { hostname: string }) {
  const [rows, setRows] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    setLoading(true)
    fetchLogs({ device_hostname: hostname, page_size: '50' })
      .then((d) => { if (active) setRows(d.results || []) })
      .catch(() => { if (active) setRows([]) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [hostname])

  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b dark:border-gray-700">
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Recent logs</div>
        <Link to={`/logs?device_hostname=${encodeURIComponent(hostname)}`}
          className="text-sm text-blue-600 dark:text-blue-400 hover:underline">View all in Logs →</Link>
      </div>
      {loading ? (
        <div className="p-6 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="p-6 text-sm text-gray-500 dark:text-gray-400">
          No logs forwarded for this server yet. (Agent log forwarding ships new log
          lines as they appear; an idle host produces few.)
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
              <tr>{['Time', 'Severity', 'Message'].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={STRIPED_ROW}>
                  <td className="px-4 py-1.5 whitespace-nowrap font-mono text-xs text-gray-500 dark:text-gray-400">{new Date(r.timestamp).toLocaleString()}</td>
                  <td className="px-4 py-1.5"><span className={severityBadge(r.severity_label || r.severity)}>{r.severity_label || r.severity}</span></td>
                  <td className="px-4 py-1.5 font-mono text-xs text-gray-700 dark:text-gray-300">{r.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function ServerDetail() {
  const { id = '' } = useParams()
  const [server, setServer] = useState<ServerDetailT>()
  // Active tab lives in the URL (?tab=Services) so a refresh restores it and the
  // URL is shareable/bookmarkable. Defaults to Overview when absent/invalid.
  const [tab, setTab] = useTabParam(TABS, 'Overview')
  const [range, setRange] = useState<TimeRange>('1h')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    fetchServer(id).then(setServer).catch(() => setError('Failed to load server.'))
  }, [id])
  useEffect(() => { load() }, [load])

  // Collector-originated ping/RTT (same source as the Servers list), keyed by
  // device_id — powers the header sparkline + the Overview RTT card.
  const [ping, setPing] = useState<PingSummary>()
  useEffect(() => {
    if (!server?.device_id) return
    let active = true
    const devId = server.device_id
    const tick = () => fetchPingSummary()
      .then((rows) => { if (active) setPing(rows.find((r) => r.device_id === devId)) })
      .catch(() => {})
    tick()
    const t = setInterval(tick, 60_000)
    return () => { active = false; clearInterval(t) }
  }, [server?.device_id])

  // One range drives every chart on the page (matches the device telemetry side).
  // All four histories load together on the Statistics tab (cpu also on Overview).
  const stats = tab === 'Statistics'
  const cpuHist = useHistory(id, 'cpu', stats || tab === 'Overview', range)
  const memHist = useHistory(id, 'memory', stats, range)
  const diskHist = useHistory(id, 'disk', stats, range)
  const netHist = useHistory(id, 'network', stats, range)

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
          <div className="text-sm text-gray-500 dark:text-gray-300 mt-1 flex flex-wrap gap-x-4">
            <span>{server.os_name || server.os || 'Unknown OS'}</span>
            <span>Arch: {server.arch || '—'}</span>
            <span>Agent {fmtVersion(server.agent_version)}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 text-sm">
          {/* Two complementary vantages: the AGENT's self-report (is it checking
              in?) and the COLLECTOR's network probe (can we reach the host?).
              They can disagree — reachable-but-not-reporting = agent crashed /
              host up; reporting-but-unreachable-network = path degrading. */}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className={`px-2 py-0.5 rounded-full text-xs ${online ? 'text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/40' : 'text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/40'}`}>
              Agent: {online ? 'reporting' : 'offline'}
            </span>
            <NetworkChip net={server.network} />
            {ping?.sparkline?.length ? (
              <PingSparkline data={ping.sparkline} color={pingColor(ping.current_ms ?? null)} />
            ) : null}
          </div>
          <div className="text-gray-500 dark:text-gray-300">Last seen: {timeAgo(server.last_seen)}</div>
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
              <div className="text-xs text-gray-500 dark:text-gray-300">Load (1m)</div>
              <div className="text-2xl font-bold text-gray-900 dark:text-gray-100 mt-1">{dm.load.load1?.toFixed(2) ?? m.load_1?.toFixed(2) ?? '—'}</div>
              <div className="text-xs text-gray-400 mt-2">5m {dm.load.load5?.toFixed(2) ?? '—'} · 15m {dm.load.load15?.toFixed(2) ?? '—'}</div>
            </div>
          </div>
          <ServerAlertingRow server={server} onChanged={load} />
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">CPU — {RANGE_LABEL[range]}</div>
            <LineChart history={cpuHist} fields={['usage_pct']} height={200} />
          </div>
          {/* Collector-originated network RTT (the Network vantage, distinct from
              the agent's self-reported metrics above). */}
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Network RTT (collector → host)</div>
              <div className="text-sm tabular-nums" style={ping?.current_ms != null ? { color: pingColor(ping.current_ms) } : undefined}>
                {ping?.current_ms != null ? `${ping.current_ms} ms`
                  : server.network?.probed === false ? 'not network-probed' : '—'}
              </div>
            </div>
            {ping?.sparkline?.length ? (
              <div className="flex items-center gap-3">
                <PingSparkline data={ping.sparkline} color={pingColor(ping.current_ms ?? null)} />
                {ping.avg_ms != null && <span className="text-xs text-gray-400">avg {ping.avg_ms}ms · max {ping.max_ms}ms</span>}
              </div>
            ) : (
              <div className="text-xs text-gray-400">
                {server.network?.probed === false
                  ? 'No routable host IP reported yet — the agent reports its own liveness above.'
                  : 'No RTT samples yet.'}
              </div>
            )}
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <InfoPanel server={server} onChanged={load} />
            <AlertsPanel server={server} />
          </div>
        </div>
      )}

      {tab === 'Statistics' && (
        <StatisticsTab dm={dm} range={range}
          cpuHist={cpuHist} memHist={memHist} diskHist={diskHist} netHist={netHist} />
      )}

      {tab === 'Processes' && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-6 text-sm text-gray-500">
          Process monitoring is not yet available. It's planned (per-process CPU /
          memory + top-by-CPU) — there's no <code>processes</code> collector or toggle yet,
          so there's nothing to enable.
        </div>
      )}

      {tab === 'Services' && <ServicesTab server={server} onTab={setTab} onChanged={load} />}
      {tab === 'Roles' && <RolesTab id={id} os={server.os} />}
      {tab === 'Config' && <ConfigTab id={id} os={server.os} onChanged={load} />}

      {tab === 'Logs' && <ServerLogsTab hostname={server.hostname} />}

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
          <dt className="text-gray-500 dark:text-gray-300">Hostname <span className="text-[10px] text-gray-400">(reported by agent)</span></dt>
          <dd className="text-gray-900 dark:text-gray-100 font-medium truncate max-w-[60%]" title={server.hostname}>{server.hostname}</dd>
        </div>
        <SiteRow server={server} onChanged={onChanged} />
        <LivenessRow server={server} onChanged={onChanged} />
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4">
            <dt className="text-gray-500 dark:text-gray-300">{k}</dt>
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
// Per-server alert silencing (observe-only + timed mute). Writes the agent's
// Device flags via /servers/{id}/alerting/; gated by agent:edit.
function ServerAlertingRow({ server, onChanged }: { server: ServerDetailT; onChanged: () => void }) {
  const canEdit = useCapabilities().includes('agent:edit')
  return (
    <AlertingControl
      alertingEnabled={server.alerting_enabled ?? true}
      silencedUntil={server.silenced_until ?? null}
      canEdit={canEdit}
      onUpdate={async (patch) => { await updateServerAlerting(server.id, patch); onChanged() }}
    />
  )
}

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
      <dt className="text-gray-500 dark:text-gray-300">Offline alert</dt>
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
          <span className="text-gray-500 dark:text-gray-300">Site</span>
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
      <dt className="text-gray-500 dark:text-gray-300">Site</dt>
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
    ? services.filter((s) =>
        s.name.toLowerCase().includes(q.toLowerCase()) ||
        (s.display_name || '').toLowerCase().includes(q.toLowerCase()))
    : services

  // Per-row watch toggle: add/remove the service in stability.services via the
  // same config PATCH (applies on the agent's next check-in). For STOPPED
  // services (not in this running list) use the text-add in the Watched section.
  const canEdit = useCapabilities().includes('agent:edit')
  const watched = new Set(server.watched_services?.configured ?? [])
  const [pending, setPending] = useState<string | null>(null)
  const toggleWatch = async (name: string) => {
    const cur = server.watched_services?.configured ?? []
    const next = watched.has(name) ? cur.filter((s) => s !== name) : [...cur, name]
    setPending(name)
    try { await updateServerConfig(server.id, { stability: { services: next } }); onChanged() }
    catch { /* error surfaced in the Watched section */ } finally { setPending(null) }
  }

  const LinkBtn = ({ to, label }: { to: Tab; label: string }) => (
    <button onClick={() => onTab(to)} className="text-blue-600 dark:text-blue-400 hover:underline font-medium">{label}</button>
  )

  let body
  if (!collected) {
    // (a) toggle OFF
    body = (
      <div className="text-sm text-gray-500 dark:text-gray-300">
        The <span className="font-medium">Services</span> collection toggle is off, so this host
        isn't reporting its running services. Enable it on the{' '}
        <LinkBtn to="Config" label="Config" /> tab to list running services here.
      </div>
    )
  } else if (services.length === 0) {
    // (b) toggle ON, no data yet
    body = (
      <div className="text-sm text-gray-500 dark:text-gray-300">
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
          <div className="max-h-96 overflow-y-auto">
            {/* Content-width table (no w-full): columns size to content, slack
                pools on the right. Zebra stripes (shared STRIPED_ROW) replace
                row dividers; whitespace-nowrap keeps columns tight. */}
            <table className={CONTENT_TABLE}>
              <thead className="text-left text-xs text-gray-500 sticky top-0 bg-white dark:bg-gray-800">
                <tr>{['Monitor?', 'Status', 'Service', 'Name', 'State', 'Start type'].map((h) => (
                  <th key={h} className="px-3 py-1 font-medium whitespace-nowrap">{canEdit || h !== 'Monitor?' ? h : ''}</th>
                ))}</tr>
              </thead>
              <tbody>
                {filtered.map((s) => {
                  // Friendly name in Service; actual name in Name. When there's no
                  // distinct friendly name, show the name in Service and leave Name
                  // blank (don't duplicate it across both columns).
                  const friendly = s.display_name && s.display_name !== s.name
                  return (
                    <tr key={s.name} className={STRIPED_ROW}>
                      <td className="px-3 py-1 text-center w-px">
                        {canEdit && (
                          <input type="checkbox" checked={watched.has(s.name)} disabled={pending === s.name}
                                 onChange={() => toggleWatch(s.name)}
                                 title="Watch this service for down/restart alerts" />
                        )}
                      </td>
                      <td className="px-3 py-1 text-center w-px">
                        <span className={`inline-block h-1.5 w-1.5 rounded-full ${s.running ? 'bg-green-500' : 'bg-gray-400'}`}
                              title={s.running ? 'running' : 'stopped'} />
                      </td>
                      <td className="px-3 py-1 font-medium text-gray-900 dark:text-gray-100 whitespace-nowrap">{friendly ? s.display_name : s.name}</td>
                      <td className="px-3 py-1 text-gray-500 dark:text-gray-300 whitespace-nowrap">{friendly ? s.name : '—'}</td>
                      <td className="px-3 py-1 text-gray-500 dark:text-gray-300 whitespace-nowrap">{s.state || (s.running ? 'running' : '—')}</td>
                      <td className="px-3 py-1 text-gray-500 dark:text-gray-300 whitespace-nowrap">{s.start_type || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-2 text-xs text-gray-400">
          {q && filtered.length > 0 && <span>{filtered.length} of {services.length} shown. </span>}
          {pending && <span className="text-amber-500">Updating “{pending}” — applies on the agent's next check-in (~30s).</span>}
        </div>
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
        <p className="text-sm text-gray-500 dark:text-gray-300 mb-3">
          No services watched yet. Add one (e.g. <code>docker</code>, <code>sshd</code>) to alert
          when it stops or restarts repeatedly — no role required.
        </p>
      ) : (
        <ul className="divide-y dark:divide-gray-700 mb-3">
          {configured.map((name) => {
            const st = byName.get(name)
            const up = st?.running === true
            const pending = !st || !st.collected_at
            const friendly = st?.display_name && st.display_name !== name
            return (
              <li key={name} className="flex items-center gap-3 py-2 text-sm">
                <span className={`h-2 w-2 rounded-full shrink-0 ${pending ? 'bg-gray-300 dark:bg-gray-600' : up ? 'bg-green-500' : 'bg-red-500'}`} />
                <span className="font-medium text-gray-900 dark:text-gray-100 w-48 truncate" title={name}>
                  {friendly ? st!.display_name : name}
                  {friendly && <span className="text-xs font-normal text-gray-400 ml-1.5">{name}</span>}
                </span>
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
  // Role-remove confirmation when the role has services under stability watch.
  const [confirm, setConfirm] = useState<{ a: AssignedRole; services: string[] } | null>(null)

  const load = useCallback(() => {
    fetchServerRoleAssignments(id).then(setAssigned).catch(() => {})
  }, [id])
  useEffect(() => { load(); fetchServerRoles().then(setAllRoles).catch(() => {}) }, [load])

  const osServices = useCallback((roleType: string): string[] => {
    const def = allRoles.find((r) => r.role_type === roleType)
    if (!def) return []
    return os === 'windows' ? def.windows_services : def.linux_services
  }, [allRoles, os])

  const assign = async (roleId: number) => {
    setBusy(true)
    try {
      const a = await assignServerRole(id, roleId)
      setNotice(a)
      load()
      setDetected((d) => d.filter((x) => x.role_id !== roleId))
    } finally { setBusy(false); setShowAssign(false); setPick('') }
  }
  // Removing a role: if any of its services are under stability watch (or it has a
  // per-server service selection), confirm first and offer to clean those up too —
  // otherwise a stale watch keeps firing flap/down alerts for an unassigned role.
  const remove = async (roleId: number) => {
    const a = assigned.find((x) => x.role_id === roleId)
    try {
      const cfg = await fetchServerConfig(id)
      const watched = cfg.stability?.services ?? []
      const svc = a ? osServices(a.role_type).filter((s) => watched.includes(s)) : []
      if (a && svc.length) { setConfirm({ a, services: svc }); return }
    } catch { /* fall through to a plain remove */ }
    await removeServerRole(id, roleId); load()
  }
  const confirmRemove = async () => {
    if (!confirm) return
    const { a, services } = confirm
    setBusy(true)
    try {
      const cfg = await fetchServerConfig(id)
      const watched = (cfg.stability?.services ?? []).filter((s) => !services.includes(s))
      const rs = { ...(cfg.role_services ?? {}) }
      delete rs[a.role_type]
      await updateServerConfig(id, { stability: { services: watched }, role_services: rs })
    } catch { /* best-effort cleanup; still remove the role */ }
    await removeServerRole(id, a.role_id)
    setConfirm(null); setBusy(false); load()
  }
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

      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => !busy && setConfirm(null)}>
          <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-5 max-w-md w-full" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Remove role “{confirm.a.name}”?</div>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              These services are under stability watch via this role. Removing the role will
              stop monitoring them (and clears this server's service selection for the role):
            </p>
            <div className="flex flex-wrap gap-1.5 my-3">
              {confirm.services.map((s) => (
                <span key={s} className="px-2 py-0.5 text-xs rounded-full bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 font-mono">{s}</span>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirm(null)} disabled={busy}
                className="px-3 py-1.5 text-sm border rounded-lg dark:border-gray-600 dark:text-gray-300 disabled:opacity-50">Cancel</button>
              <button onClick={confirmRemove} disabled={busy}
                className="px-3 py-1.5 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg disabled:opacity-50">
                {busy ? 'Removing…' : 'Remove role & stop watching'}
              </button>
            </div>
          </div>
        </div>
      )}
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
  const func = st?.functional ?? []
  const hasFunctional = func.length > 0
  const hasChecks = !!st && (st.services.length + st.ports.length + (st.custom?.length ?? 0) + func.length) > 0

  // For a role with a FUNCTIONAL result (web), the headline leads with the
  // functional verdict — does the site actually respond + is its cert valid —
  // the thing that answers "is this server working". The services/ports X/Y-pass
  // count drops to a secondary line: a containerized web server serves the site
  // without all of nginx/apache/httpd running as local services, so the process
  // count ("2/5") is misleading as the headline. Verdict = worst URL (any-of:
  // one URL down is a problem); cert = the soonest-expiring URL.
  const RANK: Record<string, number> = { healthy: 0, warning: 1, degraded: 2, down: 3 }
  const worst = hasFunctional
    ? func.reduce((w, f) => ((RANK[f.health] ?? 3) > (RANK[w.health] ?? 3) ? f : w), func[0])
    : null
  const certDays = hasFunctional
    ? func.reduce<number | null>(
        (m, f) => (typeof f.cert_days_remaining === 'number'
          ? (m === null ? f.cert_days_remaining : Math.min(m, f.cert_days_remaining))
          : m), null)
    : null
  const fVerdict = !worst ? null
    : worst.health === 'healthy'
      ? { icon: '✓', label: 'Healthy', cls: 'text-green-600 dark:text-green-400' }
    : worst.health === 'warning'
      ? { icon: '⚠', label: `Warning${worst.status_code ? ` (${worst.status_code})` : ''}`, cls: 'text-amber-600 dark:text-amber-400' }
    : worst.health === 'degraded'
      ? { icon: '⚠', label: `Degraded${worst.status_code ? ` (${worst.status_code})` : ''}`, cls: 'text-red-600 dark:text-red-400' }
      : { icon: '✗', label: `Down${worst.error ? ` — ${worst.error}` : ''}`, cls: 'text-red-600 dark:text-red-400' }

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
        className={`text-sm mt-1 flex items-center gap-1.5 flex-wrap ${hasChecks ? 'hover:underline' : 'cursor-default'} ${
          fVerdict ? fVerdict.cls : allOk ? 'text-green-600' : st && st.checks_total ? 'text-amber-600' : 'text-gray-400'}`}>
        {fVerdict ? (
          <>
            <span>{fVerdict.icon} {fVerdict.label}</span>
            {certDays !== null && (
              <span className={`text-xs ${certDays <= 30 ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'}`}>
                · 🔒 cert {certDays <= 0 ? 'EXPIRED' : `${certDays}d`}
              </span>
            )}
          </>
        ) : (
          st && st.checks_total ? `${allOk ? '✅' : '⚠️'} ${pass} pass` : 'No checks reported yet'
        )}
        {hasChecks && <span className="text-xs text-gray-400">{open ? '▾' : '▸'}</span>}
      </button>
      {/* Functional verdict leads; keep the services/ports pass count visible but demoted. */}
      {fVerdict && st && st.checks_total > 0 && (
        <div className="text-xs text-gray-400 mt-0.5">{allOk ? '✅' : '⚠️'} {pass} service/port checks pass</div>
      )}

      {open && st && (
        <div className="mt-2 border-t dark:border-gray-700 pt-2 space-y-2 text-sm">
          {st.services.length > 0 && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-0.5">Services</div>
              <ul>{st.services.map((s) => (
                <Check key={s.name} ok={!!s.running} label={s.name}
                  sub={s.running ? (s.state || 'running') : (s.state || 'not running')} />
              ))}</ul>
            </div>
          )}
          {st.ports.length > 0 && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-0.5">Ports</div>
              <ul>{st.ports.map((p) => (
                <Check key={`${p.port}/${p.proto}`} ok={!!p.open}
                  label={`${p.name ? `${p.name} ` : ''}${p.port}/${p.proto}`}
                  sub={p.open ? 'listening' : 'closed'} />
              ))}</ul>
            </div>
          )}
          {!!st.custom?.length && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-0.5">Custom</div>
              <ul>{st.custom.map((c, i) => (
                <Check key={c.name ?? i} ok={!!(c.passed ?? c.ok)} label={c.name ?? `check ${i + 1}`} />
              ))}</ul>
            </div>
          )}
          {!!st.functional?.length && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-0.5">Functional health</div>
              <ul className="space-y-0.5">{st.functional.map((f) => {
                const c = f.health === 'healthy' ? 'text-green-600 dark:text-green-400'
                  : f.health === 'warning' ? 'text-amber-600 dark:text-amber-400'
                  : 'text-red-600 dark:text-red-400'  // degraded | down
                return (
                  <li key={f.url} className="flex items-center gap-2 py-0.5 flex-wrap">
                    <span className={c}>●</span>
                    <span className="text-gray-800 dark:text-gray-200 truncate max-w-[16rem]" title={f.url}>{f.url}</span>
                    <span className={`text-xs ${c}`}>
                      {f.health}{f.status_code ? ` (${f.status_code})` : ''}{f.error ? ` — ${f.error}` : ''}
                    </span>
                    {typeof f.cert_days_remaining === 'number' && (
                      <span className={`text-xs ${f.cert_days_remaining <= 30 ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'}`}>
                        🔒 cert {f.cert_days_remaining <= 0 ? 'EXPIRED' : `${f.cert_days_remaining}d`}
                      </span>
                    )}
                  </li>
                )
              })}</ul>
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

// SSRF guardrail mirrored from the backend (is_allowed_self_url): a functional-check
// URL must be http(s) to the host ITSELF. Validate UI-side too so the operator gets
// instant feedback and we never PATCH an off-host URL (the server rejects it anyway).
function isOnHostUrl(raw: string): boolean {
  try {
    const u = new URL(raw)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
    const h = u.hostname.toLowerCase().replace(/^\[|\]$/g, '')
    return h === 'localhost' || h === '127.0.0.1' || h === '::1'
  } catch { return false }
}

type FuncMode = 'default' | 'http' | 'https' | 'custom'
const FUNC_MODES: { m: FuncMode; label: string; help: string }[] = [
  { m: 'default', label: 'Role default', help: "Derives from the Web role's ports (typically HTTP :80 + HTTPS :443 + cert)." },
  { m: 'http', label: 'HTTP-only', help: 'Checks http://localhost/ (:80). No 443, no certificate check.' },
  { m: 'https', label: 'Serves HTTPS', help: 'Checks https://localhost/ (:443) + certificate validity.' },
  { m: 'custom', label: 'Custom', help: 'Specify the exact on-host URLs/ports (e.g. :8080 / :8443).' },
]
const FUNC_PRESET: Record<Exclude<FuncMode, 'custom'>, string[]> = {
  default: [], http: ['http://localhost/'], https: ['https://localhost/'],
}

function ConfigTab({ id, os, onChanged }: { id: string; os: string; onChanged: () => void }) {
  const canEdit = useCapabilities().includes('agent:edit')
  const [cfg, setCfg] = useState<AgentDesiredConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // 'idle' | 'pending' (saved, awaiting agent check-in) | 'applied'
  const [saveState, setSaveState] = useState<'idle' | 'pending' | 'applied'>('idle')
  const [newMount, setNewMount] = useState('')
  const [mountList, setMountList] = useState<'exclude_mounts' | 'include_mounts'>('exclude_mounts')
  const [newUrl, setNewUrl] = useState('')
  // Explicit functional-mode intent. The mode is normally DERIVED from the URL
  // list, but that can't represent "Custom" when the URLs happen to equal a
  // preset (or are empty) — so picking a radio records intent here and it wins
  // over the derived value. Reset to null on (re)load so saved configs display
  // by their derived mode.
  const [modeSel, setModeSel] = useState<FuncMode | null>(null)
  // Assigned roles + role definitions, for the per-server role-service selection.
  const [roles, setRoles] = useState<AssignedRole[]>([])
  const [roleDefs, setRoleDefs] = useState<ServerRole[]>([])

  const load = useCallback(() => {
    fetchServerConfig(id).then((c) => { setCfg(c); setModeSel(null) })
      .catch(() => setError('Failed to load config.'))
  }, [id])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    fetchServerRoleAssignments(id).then(setRoles).catch(() => {})
    fetchServerRoles().then(setRoleDefs).catch(() => {})
  }, [id])

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

  // Functional web check (per-server override of the role-derived probe URLs).
  const funcUrls = cfg.functional?.web?.urls ?? []
  const funcBad = funcUrls.filter((u) => !isOnHostUrl(u))
  const derivedMode: FuncMode = funcUrls.length === 0 ? 'default'
    : funcUrls.length === 1 && funcUrls[0] === FUNC_PRESET.http[0] ? 'http'
    : funcUrls.length === 1 && funcUrls[0] === FUNC_PRESET.https[0] ? 'https'
    : 'custom'
  // Explicit intent wins; otherwise fall back to what the URLs imply.
  const funcMode: FuncMode = modeSel ?? derivedMode
  const setFunc = (urls: string[]) => { setCfg({ ...cfg, functional: { web: { urls } } }); setSaveState('idle') }
  const setFuncMode = (m: FuncMode) => {
    setModeSel(m)
    // Presets replace the URL list; Custom keeps the current URLs (so the input
    // shows even when they equal a preset, or stays empty for a fresh entry —
    // the modeSel intent keeps the Custom input open regardless of URL values).
    if (m !== 'custom') setFunc([...FUNC_PRESET[m]])
  }
  const addUrl = () => {
    const u = newUrl.trim()
    if (u && !funcUrls.includes(u)) setFunc([...funcUrls, u])
    setNewUrl('')
  }
  const removeUrl = (u: string) => setFunc(funcUrls.filter((x) => x !== u))

  // ── Per-server role-service selection (PART 2) + stability link (PART 3) ──
  // The agent reports every service its assigned roles know about; the operator
  // narrows that to what THIS host actually runs so an unselected service isn't a
  // failing "not_found" in the role's X/Y count. Empty/absent for a role = all.
  const roleSel = cfg.role_services ?? {}
  const watched = cfg.stability?.services ?? []
  const servicesForRole = (roleType: string): string[] => {
    const def = roleDefs.find((r) => r.role_type === roleType)
    if (!def) return []
    return os === 'windows' ? def.windows_services : def.linux_services
  }
  // Effective selection for a role: explicit subset if set, else "all" (= every
  // service the role defines), so an unconfigured role shows everything checked.
  const effectiveSel = (roleType: string): string[] =>
    roleSel[roleType] ?? servicesForRole(roleType)
  const toggleService = (roleType: string, name: string, on: boolean) => {
    const cur = effectiveSel(roleType)
    const next = on ? [...new Set([...cur, name])] : cur.filter((s) => s !== name)
    setCfg({ ...cfg, role_services: { ...roleSel, [roleType]: next } })
    setSaveState('idle')
  }
  const toggleWatch = (name: string, on: boolean) => {
    const next = on ? [...new Set([...watched, name])] : watched.filter((s) => s !== name)
    setCfg({ ...cfg, stability: { services: next } })
    setSaveState('idle')
  }
  // Roles that actually expose services for this OS (skip pure port/functional roles).
  const serviceRoles = roles.filter((a) => servicesForRole(a.role_type).length > 0)

  const save = async () => {
    if (!intervalValid || funcBad.length) return
    setSaving(true); setError(null)
    try {
      const updated = await updateServerConfig(id, {
        collection: cfg.collection,
        interval_seconds: cfg.interval_seconds,
        disk: cfg.disk,
        functional: { web: { urls: funcUrls } },
        role_services: cfg.role_services ?? {},
        stability: { services: watched },
      })
      setCfg(updated)
      setModeSel(null)
      setSaveState('pending')
      // Re-fetch the parent server so the Services tab's watched_services and the
      // Roles tab's role status reflect this write immediately (no hard refresh).
      onChanged()
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
        <div className="text-xs px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-300 border dark:border-gray-700">
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
        <p className="text-xs text-gray-500 dark:text-gray-300 mb-3">
          Removable/optical media is skipped automatically. Exclude drops a drive; include (if any) limits to listed drives. {mountHint}.
        </p>
        {(['exclude_mounts', 'include_mounts'] as const).map((list) => (
          <div key={list} className="mb-3">
            <div className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
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

      {/* Functional web check (per-server override of the role-derived probe URLs) */}
      <div className={card}>
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-1">Functional web check</div>
        <p className="text-xs text-gray-500 dark:text-gray-300 mb-3">
          The Web role probes this host's site over HTTP/HTTPS. If a port isn't actually
          served here, its check fails and can fire a false “site down” alert. Match the
          mode to what this host serves. URLs must point at this host (localhost / 127.0.0.1 / ::1).
        </p>
        <div className="space-y-1.5">
          {FUNC_MODES.map(({ m, label, help }) => (
            <label key={m} className="flex items-start gap-2 text-sm">
              <input type="radio" name="func-mode" checked={funcMode === m} disabled={!canEdit}
                onChange={() => setFuncMode(m)} className="mt-1 h-4 w-4 disabled:opacity-50" />
              <span>
                <span className="text-gray-800 dark:text-gray-200 font-medium">{label}</span>
                <span className="block text-xs text-gray-500 dark:text-gray-400">{help}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="mt-3">
          <div className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
            Effective check{funcMode === 'default' ? ' (role-derived)' : ''}:
          </div>
          {funcMode === 'default' ? (
            <span className="text-xs text-gray-400">Derived from the Web role's ports on this host (e.g. http://localhost:80/ + https://localhost:443/).</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {funcUrls.length === 0 && <span className="text-xs text-gray-400">none</span>}
              {funcUrls.map((u) => (
                <span key={u} className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full font-mono ${isOnHostUrl(u)
                  ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'
                  : 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300'}`}>
                  {u}
                  {canEdit && funcMode === 'custom' && <button onClick={() => removeUrl(u)} className="text-red-600 hover:text-red-800">×</button>}
                </span>
              ))}
            </div>
          )}
        </div>
        {canEdit && funcMode === 'custom' && (
          <div className="flex items-center gap-2 mt-2">
            <input value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addUrl()} placeholder="http://localhost:8080/"
              className="flex-1 px-2 py-1 text-xs border rounded dark:bg-gray-900 dark:border-gray-600 font-mono" />
            <button onClick={addUrl} className="px-2 py-1 text-xs border rounded dark:border-gray-600 dark:text-gray-300">Add</button>
          </div>
        )}
        {funcBad.length > 0 && (
          <div className="text-xs text-red-600 mt-1">Off-host URLs are not allowed: {funcBad.join(', ')}</div>
        )}
      </div>

      {/* Per-server role services (which of each role's services this host runs) +
          stability watch link. */}
      {serviceRoles.length > 0 && (
        <div className={card}>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-1">Role services</div>
          <p className="text-xs text-gray-500 dark:text-gray-300 mb-3">
            Pick which of each role's services this host actually runs — only checked
            services count toward the role's health, so an unused one (e.g. nginx on an
            Apache box) isn't a false “not found”. “Watch” adds the service to stability
            monitoring (down/flap alerts). All checked = monitor all.
          </p>
          <div className="space-y-4">
            {serviceRoles.map((a) => {
              const all = servicesForRole(a.role_type)
              const sel = effectiveSel(a.role_type)
              return (
                <div key={a.id}>
                  <div className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1.5">{a.name}</div>
                  <div className="space-y-1">
                    <div className="flex items-center justify-end gap-4 pr-0.5 text-[10px] uppercase text-gray-400">
                      <span className="w-16 text-center">Runs here</span>
                      <span className="w-12 text-center">Watch</span>
                    </div>
                    {all.map((name) => (
                      <div key={name} className="flex items-center justify-between text-sm">
                        <span className="font-mono text-gray-700 dark:text-gray-300">{name}</span>
                        <div className="flex items-center gap-4">
                          <input type="checkbox" className="w-16 h-4 disabled:opacity-50"
                            checked={sel.includes(name)} disabled={!canEdit}
                            onChange={(e) => toggleService(a.role_type, name, e.target.checked)} />
                          <input type="checkbox" className="w-12 h-4 disabled:opacity-50"
                            checked={watched.includes(name)} disabled={!canEdit}
                            onChange={(e) => toggleWatch(name, e.target.checked)} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          <p className="text-[11px] text-gray-400 mt-3">
            Checking external vhosts or a URL not served on this host? Use a Service Check
            (Checks → add an HTTP/HTTPS check) — the agent only probes this host (localhost).
          </p>
        </div>
      )}

      {canEdit && (
        <div className="flex justify-end">
          <button onClick={save} disabled={saving || !intervalValid || funcBad.length > 0}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50">
            {saving ? 'Saving…' : 'Save config'}
          </button>
        </div>
      )}
    </div>
  )
}

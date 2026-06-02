import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import StatCard from '../components/StatCard'
import EmptyState from '../components/EmptyState'
import {
  fetchDevices,
  fetchAlerts,
  fetchCheckSummary,
  fetchChecks,
  fetchDeviceReachability,
  fetchReachabilitySummary,
  checkHealth,
  checkInfraHealth,
  reachabilityOf,
  type Device,
  type Alert,
  type InfraHealth,
  type CheckSummary,
  type ServiceCheck,
  type DeviceReachability,
  type ReachabilitySummary,
} from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import clsx from 'clsx'

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
}

const REACH_PERIODS = ['1h', '6h', '24h', '7d'] as const

// Build the "Device Status Over Time" chart from the real reachability summary.
// Y-axis is integer device counts (max = total devices) so a flat line reads
// as "N devices active", not a normalised value.
function deviceStatusOption(summary: ReachabilitySummary | null): EChartsOption {
  const data = summary?.data ?? []
  const total = summary?.total_devices ?? 0
  const times = data.map((d) =>
    new Date(d.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
  return {
    title: { text: 'Device Status Over Time', textStyle: { fontSize: 14, fontWeight: 600 } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['Active', 'Unreachable'], bottom: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: times, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, min: 0, max: total || undefined },
    series: [
      {
        name: 'Active', type: 'line', smooth: true,
        data: data.map((d) => d.active),
        itemStyle: { color: '#22c55e' }, areaStyle: { opacity: 0.1 },
      },
      {
        name: 'Unreachable', type: 'line', smooth: true,
        data: data.map((d) => d.unreachable),
        itemStyle: { color: '#ef4444' }, areaStyle: { opacity: 0.1 },
      },
    ],
  }
}

const topTalkersChartOption: EChartsOption = {
  title: { text: 'Top Talkers by Bytes', textStyle: { fontSize: 14, fontWeight: 600 } },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: { left: 100, right: 20, top: 40, bottom: 30 },
  xAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${v}GB` } },
  yAxis: {
    type: 'category',
    data: ['No data yet', '', '', '', ''],
    axisLabel: { fontSize: 11 },
  },
  series: [
    {
      name: 'Bytes',
      type: 'bar',
      data: [0, 0, 0, 0, 0] as number[],
      itemStyle: { color: '#3b82f6', borderRadius: [0, 4, 4, 0] },
    },
  ],
}

// ── Infrastructure Health Card ────────────────────────────────────────────────

const INFRA_LABELS: Record<keyof InfraHealth['services'], string> = {
  postgres: 'PostgreSQL',
  valkey: 'Valkey',
  nats: 'NATS',
  influxdb: 'InfluxDB',
  opensearch: 'OpenSearch',
}

function InfraHealthSection({ health }: { health: InfraHealth | null; loading: boolean }) {
  const services = health?.services
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-5">
      <h2 className="font-semibold text-gray-800 dark:text-gray-100 mb-4">Infrastructure</h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {(Object.keys(INFRA_LABELS) as Array<keyof InfraHealth['services']>).map((key) => {
          const ok = services?.[key]
          return (
            <div
              key={key}
              className={clsx(
                'flex flex-col items-center gap-1.5 p-3 rounded-lg border text-center',
                ok === undefined
                  ? 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50'
                  : ok
                    ? 'border-green-200 bg-green-50'
                    : 'border-red-200 bg-red-50',
              )}
            >
              <span
                className={clsx(
                  'w-2.5 h-2.5 rounded-full',
                  ok === undefined ? 'bg-gray-300' : ok ? 'bg-green-500' : 'bg-red-500',
                )}
              />
              <span className="text-xs font-medium text-gray-700 dark:text-gray-300">{INFRA_LABELS[key]}</span>
              <span
                className={clsx(
                  'text-xs',
                  ok === undefined ? 'text-gray-400' : ok ? 'text-green-600' : 'text-red-600',
                )}
              >
                {ok === undefined ? '…' : ok ? 'OK' : 'Down'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate()
  const [devices, setDevices] = useState<Device[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [infraHealth, setInfraHealth] = useState<InfraHealth | null>(null)
  const [infraLoading, setInfraLoading] = useState(true)
  const [loading, setLoading] = useState(true)
  const [apiError, setApiError] = useState<string | null>(null)
  const [checkSummary, setCheckSummary] = useState<CheckSummary | null>(null)
  const [tlsChecks, setTlsChecks] = useState<ServiceCheck[]>([])
  const [reachData, setReachData] = useState<ReachabilitySummary | null>(null)
  const [reachPeriod, setReachPeriod] = useState<typeof REACH_PERIODS[number]>('1h')
  const { connected } = useWebSocket('/ws/telemetry/')

  useEffect(() => {
    fetchCheckSummary().then(setCheckSummary).catch(() => {})
    fetchChecks({ check_type: 'tls' }).then(setTlsChecks).catch(() => {})
  }, [])

  // Device-status-over-time chart: real reachability summary, refetched on
  // period change and refreshed every 60s.
  useEffect(() => {
    let cancelled = false
    const load = () => fetchReachabilitySummary(reachPeriod)
      .then((d) => { if (!cancelled) setReachData(d) }).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [reachPeriod])

  // TLS checks within their warn window (days_remaining set + below threshold),
  // soonest first.
  const expiringCerts = tlsChecks
    .map((c) => ({ c, days: typeof c.last_details?.days_remaining === 'number' ? (c.last_details.days_remaining as number) : null }))
    .filter((x) => x.days != null && x.days <= 30)
    .sort((a, b) => (a.days as number) - (b.days as number))

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    Promise.allSettled([fetchDevices(), fetchAlerts()])
      .then(([devResult, alertResult]) => {
        if (cancelled) return
        if (devResult.status === 'fulfilled') {
          // Defensive: always ensure we have an array
          const results = devResult.value?.results
          setDevices(Array.isArray(results) ? results : [])
        }
        if (alertResult.status === 'fulfilled') {
          setAlerts(Array.isArray(alertResult.value) ? alertResult.value : [])
        }
        if (devResult.status === 'rejected' && alertResult.status === 'rejected') {
          setApiError('Could not reach the API. Check that the backend is running.')
        }
        setLoading(false)
      })
      .catch(() => {
        if (!cancelled) {
          setApiError('Could not reach the API. Check that the backend is running.')
          setLoading(false)
        }
      })

    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    setInfraLoading(true)
    Promise.allSettled([checkHealth(), checkInfraHealth()])
      .then(([, infraResult]) => {
        if (cancelled) return
        if (infraResult.status === 'fulfilled') setInfraHealth(infraResult.value)
        setInfraLoading(false)
      })
      .catch(() => { if (!cancelled) setInfraLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Ensure these are always arrays before calling array methods
  const safeDevices = Array.isArray(devices) ? devices : []
  const safeAlerts = Array.isArray(alerts) ? alerts : []

  const activeAlerts = safeAlerts.filter((a) => a.state === 'firing')
  const criticalCount = activeAlerts.filter((a) => a.severity === 'critical').length
  const recentAlerts = safeAlerts.slice(0, 5)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-500 dark:text-gray-400">Loading dashboard…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Dashboard</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">Network overview at a glance</p>
        </div>
        {connected && (
          <span className="flex items-center gap-1.5 text-xs font-medium text-green-600 bg-green-50 px-2.5 py-1 rounded-full border border-green-200">
            <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
            Live
          </span>
        )}
      </div>

      {/* API error banner */}
      {apiError && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 flex items-center gap-2">
          <span>⚠️</span>
          <span>{apiError}</span>
        </div>
      )}

      {/* Infrastructure health */}
      <InfraHealthSection health={infraHealth} loading={infraLoading} />

      {/* Device reachability summary */}
      {safeDevices.length > 0 && (() => {
        const reach = safeDevices.map(reachabilityOf)
        const up = reach.filter((r) => r === 'reachable').length
        const degraded = reach.filter((r) => r === 'degraded').length
        const down = reach.filter((r) => r === 'unreachable').length
        return (
          <div className="flex flex-wrap items-center gap-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3 text-sm">
            <span className="font-medium text-gray-700 dark:text-gray-200">{safeDevices.length} devices</span>
            <span className="inline-flex items-center gap-1.5 text-green-600 dark:text-green-400"><span className="w-2 h-2 rounded-full bg-green-500" />{up} reachable</span>
            {degraded > 0 && <span className="inline-flex items-center gap-1.5 text-yellow-600 dark:text-yellow-500"><span className="w-2 h-2 rounded-full bg-yellow-500" />{degraded} degraded</span>}
            <span className={clsx('inline-flex items-center gap-1.5', down > 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>
              <span className={clsx('w-2 h-2 rounded-full', down > 0 ? 'bg-red-500' : 'bg-gray-300')} />{down} unreachable {down > 0 ? '⚠️' : ''}
            </span>
          </div>
        )
      })()}

      {/* Stat cards — always visible even with no devices */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Devices"
          value={safeDevices.length}
          subtitle={safeDevices.length === 0 ? 'none managed yet' : 'managed devices'}
          color="blue"
          action={safeDevices.length === 0 ? { label: 'Add a device', href: '/devices' } : undefined}
        />
        <StatCard
          title="Active Alerts"
          value={activeAlerts.length}
          subtitle={`${criticalCount} critical`}
          color={criticalCount > 0 ? 'red' : activeAlerts.length > 0 ? 'yellow' : 'green'}
          action={{ label: 'View alerts', href: '/alerts' }}
        />
        <StatCard
          title="CVEs"
          value={0}
          subtitle="no data yet"
          color="yellow"
          action={{ label: 'Configure CVE feed', href: '/settings' }}
        />
        <StatCard
          title="Service Checks"
          value={checkSummary ? checkSummary.total : '—'}
          subtitle={checkSummary
            ? (checkSummary.down > 0
              ? `${checkSummary.down} down`
              : checkSummary.degraded > 0
                ? `${checkSummary.degraded} degraded`
                : 'all healthy')
            : 'no checks yet'}
          color={checkSummary && checkSummary.down > 0
            ? 'red'
            : checkSummary && checkSummary.degraded > 0
              ? 'yellow'
              : 'green'}
          action={{ label: 'View checks', href: '/checks' }}
        />
      </div>

      {/* Certificates expiring soon (from TLS service checks) */}
      {expiringCerts.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <h2 className="font-semibold text-gray-900 dark:text-gray-100">Certificates Expiring Soon</h2>
            <a href="/checks" className="text-sm text-blue-600 hover:text-blue-800">View checks</a>
          </div>
          <ul className="divide-y divide-gray-100 dark:divide-gray-700">
            {expiringCerts.map(({ c, days }) => (
              <li key={c.id} className="flex items-center gap-2 px-5 py-2.5 text-sm">
                <span>{(days as number) <= 7 ? '🔴' : '⚠️'}</span>
                <span className="font-mono text-gray-700 dark:text-gray-300">{c.host}</span>
                <span className={clsx('ml-auto font-semibold',
                  (days as number) <= 7 ? 'text-red-600 dark:text-red-400' : 'text-yellow-600 dark:text-yellow-400')}>
                  {days} day{days === 1 ? '' : 's'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Empty state when no devices */}
      {safeDevices.length === 0 && !apiError && (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
          <EmptyState
            title="No devices yet"
            description="Add your first network device to start seeing telemetry, alerts, and health data on this dashboard."
            action={{ label: 'Add Your First Device', onClick: () => navigate('/devices') }}
            icon="📡"
          />
        </div>
      )}

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex justify-end gap-1 mb-1">
            {REACH_PERIODS.map((p) => (
              <button key={p} onClick={() => setReachPeriod(p)}
                className={clsx('px-2 py-0.5 text-xs rounded-md border',
                  reachPeriod === p
                    ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                    : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800')}>
                {p}
              </button>
            ))}
          </div>
          <ReactECharts
            option={deviceStatusOption(reachData)}
            style={{ height: 240 }}
            opts={{ renderer: 'svg' }}
            notMerge
          />
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <ReactECharts
            option={topTalkersChartOption}
            style={{ height: 240 }}
            opts={{ renderer: 'svg' }}
          />
        </div>
      </div>

      {/* Device latency sparklines */}
      {safeDevices.length > 0 && <DeviceLatencyWidget devices={safeDevices} />}

      {/* Recent alerts table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800 dark:text-gray-100">Recent Alerts</h2>
          <a href="/alerts" className="text-sm text-blue-600 hover:text-blue-800">
            View all
          </a>
        </div>
        {recentAlerts.length === 0 ? (
          <EmptyState
            title="No active alerts"
            description="Your network is healthy. Alerts will appear here when triggered."
            icon="✅"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Rule</th>
                  <th className="px-5 py-3 font-medium">Device</th>
                  <th className="px-5 py-3 font-medium">Fired At</th>
                  <th className="px-5 py-3 font-medium">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {recentAlerts.map((alert) => (
                  <tr key={alert.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          SEVERITY_COLORS[alert.severity] ?? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400',
                        )}
                      >
                        {alert.severity}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-800 dark:text-gray-100">{alert.rule_name}</td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{alert.device}</td>
                    <td className="px-5 py-3 text-gray-500 dark:text-gray-400">
                      {new Date(alert.fired_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          alert.state === 'firing'
                            ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                            : alert.state === 'acknowledged'
                              ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
                              : 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
                        )}
                      >
                        {alert.state}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// Per-device current RTT + a 1h latency sparkline. Color follows the same zones
// as the device Telemetry chart (<10ms green · 10-50 blue · 50-100 yellow · >100 red).
function latencyColor(rtt: number | null | undefined): string {
  if (rtt == null) return '#9ca3af'
  if (rtt < 10) return '#22c55e'
  if (rtt < 50) return '#3b82f6'
  if (rtt < 100) return '#eab308'
  return '#ef4444'
}

function LatencySpark({ data }: { data: DeviceReachability['data'] }) {
  const pts = data.filter((p) => p.rtt_ms != null)
  if (pts.length < 2) {
    return <span className="text-[10px] text-gray-300 dark:text-gray-600">no data</span>
  }
  const option: EChartsOption = {
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: 'category', show: false, data: pts.map((p) => p.time) },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${new Date(p.axisValue).toLocaleString()}<br/>${Number(p.value).toFixed(1)} ms`
      },
    },
    series: [{
      type: 'line', data: pts.map((p) => p.rtt_ms), showSymbol: false, smooth: true,
      lineStyle: { color: '#3b82f6', width: 1.25 }, areaStyle: { color: '#3b82f6', opacity: 0.1 },
    }],
  }
  return <ReactECharts option={option} style={{ height: 24, width: 120 }} opts={{ renderer: 'svg' }} notMerge />
}

function DeviceLatencyWidget({ devices }: { devices: Device[] }) {
  const [data, setData] = useState<Record<number, DeviceReachability>>({})
  const list = devices.slice(0, 8)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      Promise.allSettled(list.map((d) => fetchDeviceReachability(d.id, '1h').then((r) => [d.id, r] as const)))
        .then((res) => {
          if (cancelled) return
          const m: Record<number, DeviceReachability> = {}
          for (const r of res) if (r.status === 'fulfilled') m[r.value[0]] = r.value[1]
          setData(m)
        })
    }
    load()
    const t = setInterval(load, 30_000)
    return () => { cancelled = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices.map((d) => d.id).join(',')])

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
        <h2 className="font-semibold text-gray-800 dark:text-gray-100">Device Latency</h2>
      </div>
      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {list.map((d) => {
          const r = data[d.id]
          const rtt = r?.rtt_ms ?? null
          return (
            <div key={d.id} className="flex items-center gap-3 px-5 py-2 text-sm">
              <a href={`/devices/${d.id}`} className="flex-1 min-w-0 truncate text-gray-700 dark:text-gray-300 hover:text-blue-600">{d.hostname}</a>
              <div className="shrink-0"><LatencySpark data={r?.data ?? []} /></div>
              <span className="w-16 text-right font-mono font-medium" style={{ color: latencyColor(rtt) }}>
                {rtt != null ? `${rtt.toFixed(1)}ms` : '—'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

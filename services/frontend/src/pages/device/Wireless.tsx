import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import clsx from 'clsx'
import {
  fetchDeviceUnifiAp,
  type DeviceDetail, type UnifiApDetail, type MetricPoint,
} from '../../api/client'

const PERIODS = ['1h', '6h', '24h', '7d'] as const
type Period = (typeof PERIODS)[number]

// Distinct line color per radio band so multi-band charts stay legible.
const BAND_COLOR: Record<string, string> = {
  '2.4GHz': '#f59e0b',
  '5GHz': '#3b82f6',
  '6GHz': '#8b5cf6',
}

function fmtUptime(secs: number | null | undefined): string {
  if (!secs || secs <= 0) return '—'
  const d = Math.floor(secs / 86400)
  const h = Math.floor((secs % 86400) / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300'
  if (score >= 90) return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
  if (score >= 70) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
  return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
}

function HealthStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs uppercase tracking-wide text-gray-400 dark:text-gray-500">{label}</span>
      <span className="text-lg font-semibold text-gray-800 dark:text-gray-100">{value}</span>
    </div>
  )
}

/** Multi-series time chart: one line per band (or a single series). */
function BandChart({ title, unit, series }: {
  title: string
  unit: string
  series: { name: string; color: string; data: MetricPoint[] }[]
}) {
  const hasData = series.some((s) => s.data.length > 0)
  const option: EChartsOption = {
    grid: { left: 46, right: 14, top: 28, bottom: 26 },
    legend: { show: series.length > 1, top: 0, right: 0, textStyle: { fontSize: 10 }, itemHeight: 8 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: unit, nameTextStyle: { fontSize: 10 }, min: 0, scale: true, axisLabel: { fontSize: 10 } },
    series: series.map((s) => ({
      name: s.name, type: 'line', showSymbol: false, smooth: true,
      data: s.data.map((p) => [p.time, p.value]),
      lineStyle: { width: 1.5, color: s.color }, itemStyle: { color: s.color },
      areaStyle: { opacity: 0.06, color: s.color },
    })),
  }
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">{title}</div>
      {hasData
        ? <ReactECharts option={option} style={{ height: 200 }} opts={{ renderer: 'svg' }} notMerge />
        : <div className="h-[200px] flex items-center justify-center text-xs text-gray-400">No data for this range</div>}
    </div>
  )
}

export default function Wireless({ device }: { device: DeviceDetail }) {
  const [data, setData] = useState<UnifiApDetail | null>(null)
  const [period, setPeriod] = useState<Period>('1h')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const load = () => fetchDeviceUnifiAp(device.id, period)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [device.id, period])

  const status = data?.status ?? null
  const ts = data?.timeseries

  if (loading && !data) {
    return <div className="py-16 flex justify-center"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }

  if (!status) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-10 text-center">
        <div className="text-3xl mb-2">📶</div>
        <div className="font-medium text-gray-700 dark:text-gray-200">No wireless telemetry yet</div>
        <div className="text-sm text-gray-500 mt-1">
          The UniFi telemetry collector refreshes AP radio/health stats every few minutes.
          Check back shortly, or verify the controller in Settings → Integrations.
        </div>
      </div>
    )
  }

  const bands = ts ? Object.keys(ts.radios).sort() : []

  return (
    <div className="space-y-5">
      {/* Health summary */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">AP Health</h3>
          <span className={clsx('px-2 py-0.5 rounded-full text-xs font-semibold', scoreColor(status.satisfaction))}>
            Score {status.satisfaction ?? '—'}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          <HealthStat label="CPU" value={status.cpu_pct != null ? `${Math.round(status.cpu_pct)}%` : '—'} />
          <HealthStat label="Memory" value={status.memory_pct != null ? `${Math.round(status.memory_pct)}%` : '—'} />
          <HealthStat label="Temp" value={status.temperature_c != null ? `${Math.round(status.temperature_c)}°C` : '—'} />
          <HealthStat label="Uptime" value={fmtUptime(status.uptime_seconds)} />
          <HealthStat label="Clients" value={String(status.client_count)} />
          <HealthStat
            label="Uplink"
            value={status.uplink_speed_mbps ? `${status.uplink_speed_mbps >= 1000 ? status.uplink_speed_mbps / 1000 + 'G' : status.uplink_speed_mbps + 'M'} ${status.uplink_type || ''}`.trim() : '—'}
          />
        </div>
      </div>

      {/* Radio status table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-700 font-semibold text-gray-800 dark:text-gray-100">
          Radio Status
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                <th className="px-5 py-2 font-medium">Band</th>
                <th className="px-5 py-2 font-medium">Channel</th>
                <th className="px-5 py-2 font-medium">Clients</th>
                <th className="px-5 py-2 font-medium">Chan Util</th>
                <th className="px-5 py-2 font-medium">Noise</th>
                <th className="px-5 py-2 font-medium">TX Power</th>
                <th className="px-5 py-2 font-medium">Retries</th>
                <th className="px-5 py-2 font-medium">Score</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {status.radios.length === 0 ? (
                <tr><td colSpan={8} className="px-5 py-4 text-center text-gray-400">No radio data</td></tr>
              ) : status.radios.map((r) => (
                <tr key={r.band}>
                  <td className="px-5 py-2 font-medium text-gray-800 dark:text-gray-100">{r.band}</td>
                  <td className="px-5 py-2">{r.channel ?? '—'}{r.channel_width ? ` (${r.channel_width})` : ''}</td>
                  <td className="px-5 py-2">{r.clients}</td>
                  <td className="px-5 py-2">{r.channel_utilization_pct != null ? `${Math.round(r.channel_utilization_pct)}%` : '—'}</td>
                  <td className="px-5 py-2">{r.noise_floor_dbm != null ? `${r.noise_floor_dbm} dBm` : '—'}</td>
                  <td className="px-5 py-2">{r.tx_power_dbm != null ? `${r.tx_power_dbm} dBm` : '—'}</td>
                  <td className="px-5 py-2">{r.tx_retries_pct != null ? `${r.tx_retries_pct}%` : '—'}</td>
                  <td className="px-5 py-2">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-semibold', scoreColor(r.satisfaction))}>
                      {r.satisfaction ?? '—'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Period selector + charts */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">Range:</span>
        {PERIODS.map((p) => (
          <button key={p} onClick={() => setPeriod(p)}
            className={clsx('px-2.5 py-1 text-xs rounded-md border',
              period === p ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300')}>
            {p}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BandChart title="Client count" unit="clients"
          series={[{ name: 'Clients', color: '#10b981', data: ts?.clients_total ?? [] }]} />
        <BandChart title="Channel utilization" unit="%"
          series={bands.map((b) => ({ name: b, color: BAND_COLOR[b] ?? '#64748b', data: ts!.radios[b].channel_utilization_pct }))} />
        <BandChart title="TX throughput" unit="bytes/win"
          series={bands.map((b) => ({ name: b, color: BAND_COLOR[b] ?? '#64748b', data: ts!.radios[b].tx_bytes }))} />
        <BandChart title="RX throughput" unit="bytes/win"
          series={bands.map((b) => ({ name: b, color: BAND_COLOR[b] ?? '#64748b', data: ts!.radios[b].rx_bytes }))} />
      </div>
    </div>
  )
}

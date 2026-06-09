import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import clsx from 'clsx'
import {
  fetchDeviceUnifiConsole,
  type DeviceDetail, type UnifiConsoleDetail, type MetricPoint,
} from '../../api/client'

const PERIODS = ['1h', '6h', '24h', '7d'] as const
type Period = (typeof PERIODS)[number]

function fmtBps(bps: number | null | undefined): string {
  if (!bps) return '0'
  const u = ['bps', 'Kbps', 'Mbps', 'Gbps']
  let v = bps * 8, i = 0  // stat rate is bytes/s → bits/s
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++ }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${u[i]}`
}

function LineChart({ title, unit, series }: {
  title: string; unit: string; series: { name: string; color: string; data: MetricPoint[] }[]
}) {
  const hasData = series.some((s) => s.data.length > 0)
  const option: EChartsOption = {
    grid: { left: 48, right: 14, top: 26, bottom: 24 },
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
        ? <ReactECharts option={option} style={{ height: 180 }} opts={{ renderer: 'svg' }} notMerge />
        : <div className="h-[180px] flex items-center justify-center text-xs text-gray-400">No data for this range</div>}
    </div>
  )
}

/** Controller-status + WAN panels + charts for a UniFi console (UDM, Cloud Key …). */
export default function ConsoleTelemetry({ device }: { device: DeviceDetail }) {
  const [data, setData] = useState<UnifiConsoleDetail | null>(null)
  const [period, setPeriod] = useState<Period>('1h')

  useEffect(() => {
    let cancelled = false
    const load = () => fetchDeviceUnifiConsole(device.id, period)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [device.id, period])

  const s = data?.status
  if (!s) {
    return (
      <div className="lg:col-span-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 text-sm text-gray-500 dark:text-gray-400">
        Controller telemetry not collected yet — the UniFi telemetry task refreshes console health every few minutes.
      </div>
    )
  }
  const ts = data?.timeseries
  const wanKeys = ts ? Object.keys(ts.wan).sort() : []
  const WAN_COLOR = ['#3b82f6', '#f59e0b']

  return (
    <div className="lg:col-span-3 space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Controller status */}
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Controller Status</h3>
            <span className={clsx('inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full',
              s.state === 1 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400')}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', s.state === 1 ? 'bg-green-500' : 'bg-red-500')} />
              {s.state === 1 ? 'Running' : 'Down'}
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <Info label="Network version" value={s.os_version || '—'} />
            <Info label="Satisfaction" value={s.satisfaction != null ? `${s.satisfaction}%` : '—'} />
            <Info label="Adopted devices" value={String(s.num_adopted)} />
            <Info label="Disconnected" value={String(s.num_disconnected)} />
            <Info label="CPU" value={s.cpu_pct != null ? `${Math.round(s.cpu_pct)}%` : '—'} />
            <Info label="Memory" value={s.memory_pct != null ? `${Math.round(s.memory_pct)}%` : '—'} />
            <Info label="Load (1m)" value={s.loadavg_1 != null ? s.loadavg_1.toFixed(2) : '—'} />
            <Info label="Temp" value={s.temperature_c != null ? `${Math.round(s.temperature_c)}°C` : '—'} />
          </dl>
        </div>

        {/* WAN status */}
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 text-sm font-semibold text-gray-800 dark:text-gray-100">WAN Status</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                  <th className="px-4 py-2 font-medium">WAN</th>
                  <th className="px-4 py-2 font-medium">IP</th>
                  <th className="px-4 py-2 font-medium">Latency</th>
                  <th className="px-4 py-2 font-medium">↓ / ↑</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {s.wans.length === 0 ? (
                  <tr><td colSpan={4} className="px-4 py-4 text-center text-gray-400">No WAN interfaces</td></tr>
                ) : s.wans.map((w) => (
                  <tr key={w.key}>
                    <td className="px-4 py-2 font-medium">{w.name} {w.up ? '✅' : '🔴'}</td>
                    <td className="px-4 py-2 font-mono text-xs">{w.ip || '—'}</td>
                    <td className="px-4 py-2">{w.latency_ms != null ? `${Math.round(w.latency_ms)}ms` : '—'}</td>
                    <td className="px-4 py-2 text-xs whitespace-nowrap">{fmtBps(w.rx_bps)} / {fmtBps(w.tx_bps)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Period + charts */}
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
        <LineChart title="CPU" unit="%" series={[{ name: 'CPU', color: '#3b82f6', data: ts?.health.cpu_pct ?? [] }]} />
        <LineChart title="Memory" unit="%" series={[{ name: 'Memory', color: '#10b981', data: ts?.health.memory_pct ?? [] }]} />
        <LineChart title="WAN latency" unit="ms"
          series={wanKeys.map((k, i) => ({ name: k, color: WAN_COLOR[i % WAN_COLOR.length], data: ts!.wan[k].latency_ms }))} />
        <LineChart title="WAN throughput (rx)" unit="B/s"
          series={wanKeys.map((k, i) => ({ name: k, color: WAN_COLOR[i % WAN_COLOR.length], data: ts!.wan[k].rx_bps }))} />
      </div>
    </div>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt>
      <dd className="text-gray-800 dark:text-gray-100">{value}</dd>
    </div>
  )
}

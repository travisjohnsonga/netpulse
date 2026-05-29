import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import clsx from 'clsx'
import { useWebSocket } from '../../hooks/useWebSocket'
import { type DeviceDetail } from '../../api/client'

// The telemetry metrics API/InfluxDB query layer isn't implemented yet
// (/api/telemetry/metrics/ returns 501), so this tab renders illustrative
// series with a deterministic shape. The WebSocket indicator reflects the live
// telemetry channel; series swap to real data once the metrics API lands.

// Deterministic pseudo-series so the charts are stable across renders/SSR.
function series(seed: number, base: number, amp: number, n = 24): number[] {
  return Array.from({ length: n }, (_, i) => {
    const v = base + amp * Math.sin((i + seed) / 3) + amp * 0.4 * Math.sin((i + seed) / 1.5)
    return Math.max(0, Math.min(100, Math.round(v)))
  })
}

const HOURS = Array.from({ length: 24 }, (_, i) => `${(i - 23 + 24) % 24}:00`)

function lineOption(name: string, color: string, data: number[]): EChartsOption {
  return {
    grid: { left: 36, right: 12, top: 28, bottom: 24 },
    tooltip: { trigger: 'axis' },
    title: { text: name, textStyle: { fontSize: 13, color: '#374151' }, left: 0, top: 0 },
    xAxis: { type: 'category', data: HOURS, axisLabel: { fontSize: 10, color: '#9ca3af', interval: 5 } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { fontSize: 10, color: '#9ca3af', formatter: '{value}%' } },
    series: [{ name, type: 'line', smooth: true, showSymbol: false, data, areaStyle: { opacity: 0.12, color }, lineStyle: { color, width: 2 }, itemStyle: { color } }],
  }
}

function sparkOption(data: number[], color: string): EChartsOption {
  return {
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    xAxis: { type: 'category', show: false, data: data.map((_, i) => i) },
    yAxis: { type: 'value', show: false, min: 0, max: 100 },
    series: [{ type: 'line', data, showSymbol: false, smooth: true, lineStyle: { color, width: 1.5 }, areaStyle: { opacity: 0.15, color } }],
  }
}

const IFACES = [
  { name: 'GigabitEthernet0/0/0', speed: '10G', seed: 1, util: 42 },
  { name: 'GigabitEthernet0/0/1', speed: '10G', seed: 5, util: 78 },
  { name: 'GigabitEthernet0/1/0', speed: '1G', seed: 9, util: 12 },
  { name: 'TenGigE0/2/0', speed: '10G', seed: 3, util: 91 },
]

function utilColor(u: number): string {
  if (u >= 90) return '#ef4444'
  if (u >= 80) return '#f97316'
  if (u >= 60) return '#eab308'
  return '#22c55e'
}

export default function Telemetry({ device }: { device: DeviceDetail }) {
  const { connected } = useWebSocket(`/ws/telemetry/${device.id}/`)
  const cpu = series(device.id + 2, 45, 25)
  const mem = series(device.id + 7, 60, 15)

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs">
        <span className={clsx('w-2 h-2 rounded-full', connected ? 'bg-green-400 animate-pulse' : 'bg-gray-400')} />
        <span className={connected ? 'text-green-600' : 'text-gray-400'}>{connected ? 'Live telemetry connected' : 'Telemetry channel offline'}</span>
        <span className="text-gray-400">· illustrative data until the metrics API lands</span>
      </div>

      {/* CPU / memory */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <ReactECharts option={lineOption('CPU — last 24h', '#3b82f6', cpu)} style={{ height: 220 }} opts={{ renderer: 'svg' }} />
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <ReactECharts option={lineOption('Memory — last 24h', '#8b5cf6', mem)} style={{ height: 220 }} opts={{ renderer: 'svg' }} />
        </div>
      </div>

      {/* Interfaces */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-200"><h3 className="text-sm font-semibold text-gray-800">Interfaces</h3></div>
        <div className="divide-y divide-gray-100">
          {IFACES.map((iface) => (
            <div key={iface.name} className="flex items-center gap-4 px-5 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs text-gray-800 truncate">{iface.name}</p>
                <p className="text-xs text-gray-400">{iface.speed}</p>
              </div>
              <div className="w-32 h-8 shrink-0">
                <ReactECharts option={sparkOption(series(iface.seed, iface.util, 12), utilColor(iface.util))} style={{ height: 32 }} opts={{ renderer: 'svg' }} />
              </div>
              <span className="text-sm font-medium w-12 text-right" style={{ color: utilColor(iface.util) }}>{iface.util}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

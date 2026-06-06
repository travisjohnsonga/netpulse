import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import clsx from 'clsx'
import FlowsTable from '../../components/FlowsTable'
import FlowSankey from '../../components/FlowSankey'
import {
  fetchFlows,
  fetchFlowSummary,
  fetchFlowDeviceSummary,
  fetchFlowSankey,
  type DeviceDetail,
  type FlowTrafficPoint,
  type FlowProtocolMix,
  type FlowConversation,
} from '../../api/client'
import { fmtBytes } from '../../lib/bytes'
import { useIsDark, chartColors } from '../../lib/useIsDark'

const WINDOWS = ['1h', '6h', '24h', '7d'] as const
type Window = (typeof WINDOWS)[number]

// Inbound = traffic to this device, outbound = traffic from it.
const INBOUND = '#10b981'
const OUTBOUND = '#3b82f6'
const PROTO_COLORS: Record<string, string> = { TCP: '#3b82f6', UDP: '#10b981', ICMP: '#f59e0b', Other: '#64748b' }

function trafficOption(points: FlowTrafficPoint[], isDark: boolean): EChartsOption {
  const c = chartColors(isDark)
  const axisLabel = { color: c.muted, fontSize: 10 }
  const area = (color: string) => ({ opacity: 0.18, color })
  return {
    color: [INBOUND, OUTBOUND],
    grid: { left: 56, right: 16, top: 28, bottom: 28 },
    legend: { top: 0, right: 0, textStyle: { fontSize: 11, color: c.text }, icon: 'roundRect' },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params]
        const rows = arr.map((p: any) => `${p.marker} ${p.seriesName}: ${fmtBytes(p.value[1])}`).join('<br/>')
        const t = new Date(arr[0].value[0]).toLocaleString()
        return `${t}<br/>${rows}`
      },
    },
    xAxis: { type: 'time', axisLabel },
    yAxis: { type: 'value', min: 0, axisLabel: { ...axisLabel, formatter: (v: number) => fmtBytes(v) }, splitLine: { lineStyle: { color: c.split } } },
    series: [
      { name: 'Inbound', type: 'line', smooth: true, showSymbol: false, areaStyle: area(INBOUND), lineStyle: { width: 1.5 }, data: points.map((p) => [p.timestamp, p.inbound_bytes]) },
      { name: 'Outbound', type: 'line', smooth: true, showSymbol: false, areaStyle: area(OUTBOUND), lineStyle: { width: 1.5 }, data: points.map((p) => [p.timestamp, p.outbound_bytes]) },
    ],
  }
}

function protocolOption(mix: FlowProtocolMix[], isDark: boolean): EChartsOption {
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.name}: ${fmtBytes(p.data.value)} (${p.data.pct}%)<br/>${p.data.flows.toLocaleString()} flows`,
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11, color: chartColors(isDark).text } },
    series: [
      {
        name: 'Protocol',
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: 'transparent', borderWidth: 2 },
        label: { show: false },
        data: mix.map((m) => ({
          name: m.protocol,
          value: m.bytes,
          pct: m.pct,
          flows: m.flows,
          itemStyle: { color: PROTO_COLORS[m.protocol] ?? PROTO_COLORS.Other },
        })),
      },
    ],
  }
}

function conversationsOption(convos: FlowConversation[], isDark: boolean): EChartsOption {
  const c = chartColors(isDark)
  // Largest at the top: ECharts category axis draws index 0 at the bottom.
  const rows = [...convos].reverse()
  return {
    grid: { left: 8, right: 64, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.data.src} → ${p.data.dst}<br/>Bytes: ${fmtBytes(p.data.value)}<br/>Packets: ${p.data.packets.toLocaleString()}<br/>Flows: ${p.data.flows.toLocaleString()}`,
    },
    xAxis: { type: 'value', axisLabel: { color: c.muted, fontSize: 10, formatter: (v: number) => fmtBytes(v) }, splitLine: { lineStyle: { color: c.split } } },
    yAxis: {
      type: 'category',
      data: rows.map((r) => `${r.src_ip} → ${r.dst_ip}`),
      axisLabel: { color: c.text, fontSize: 11, fontFamily: 'monospace' },
    },
    series: [
      {
        type: 'bar',
        barWidth: '60%',
        itemStyle: { color: OUTBOUND, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', formatter: (p: any) => fmtBytes(p.data.value), fontSize: 10, color: c.muted },
        data: rows.map((r) => ({ value: r.bytes, src: r.src_ip, dst: r.dst_ip, packets: r.packets, flows: r.flows })),
      },
    ],
  }
}

// Device "Flows" tab — flows involving this device (src OR dst = device IP).
export default function Flows({ device }: { device: DeviceDetail }) {
  const [window, setWindow] = useState<Window>('1h')
  const isDark = useIsDark()
  const deviceId = String(device.id)
  const deviceIp = device.management_ip || device.ip_address

  const summaryQ = useQuery({
    queryKey: ['device-flow-summary', deviceId, window],
    queryFn: () => fetchFlowSummary({ window, device_id: deviceId }),
  })
  const chartsQ = useQuery({
    queryKey: ['device-flow-charts', deviceId, window],
    queryFn: () => fetchFlowDeviceSummary({ window, device_id: deviceId }),
  })
  const sankeyQ = useQuery({
    queryKey: ['device-flow-sankey', deviceId, window],
    queryFn: () => fetchFlowSankey({ window, device_id: deviceId, limit: '30' }),
  })
  const flowsQ = useQuery({
    queryKey: ['device-flows', deviceId, window],
    queryFn: () => fetchFlows({ window, device_id: deviceId, limit: '200' }),
  })

  const summary = summaryQ.data
  const charts = chartsQ.data
  const traffic = charts?.traffic_over_time ?? []
  const protocols = charts?.protocol_mix ?? []
  const convos = charts?.top_conversations ?? []

  return (
    <div className="space-y-4">
      {/* Header + window selector */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-4 text-sm">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">Flows — {device.hostname}</h3>
          {summary && (
            <span className="text-gray-500 dark:text-gray-400">
              {summary.total_flows.toLocaleString()} flows · {fmtBytes(summary.total_bytes)} · {summary.unique_dst_ips} destinations
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded-md border',
                window === w
                  ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                  : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50',
              )}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {/* Traffic Flow sankey (full width) */}
      <FlowSankey data={sankeyQ.data} loading={sankeyQ.isLoading} deviceIp={deviceIp} />

      {/* Traffic over time + protocol mix */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100 mb-1">Traffic Over Time</h3>
          {chartsQ.isLoading ? (
            <div className="h-[240px] flex items-center justify-center"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
          ) : traffic.length === 0 ? (
            <p className="h-[240px] flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">No flow data in this window.</p>
          ) : (
            <ReactECharts option={trafficOption(traffic, isDark)} style={{ height: 240 }} opts={{ renderer: 'svg' }} notMerge />
          )}
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100 mb-1">Protocol Mix</h3>
          {protocols.length === 0 ? (
            <p className="h-[240px] flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">No flow data in this window.</p>
          ) : (
            <ReactECharts option={protocolOption(protocols, isDark)} style={{ height: 240 }} opts={{ renderer: 'svg' }} notMerge />
          )}
        </div>
      </div>

      {/* Top conversations */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="font-semibold text-gray-800 dark:text-gray-100 mb-1">Top Conversations</h3>
        {convos.length === 0 ? (
          <p className="h-[180px] flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">No flow data in this window.</p>
        ) : (
          <ReactECharts option={conversationsOption(convos, isDark)} style={{ height: Math.max(140, convos.length * 38) }} opts={{ renderer: 'svg' }} notMerge />
        )}
      </div>

      {/* Recent flows table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">Recent Flows</h3>
        </div>
        <FlowsTable rows={flowsQ.data?.results ?? []} loading={flowsQ.isLoading} maxHeight="max-h-[32rem]" />
        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
          {(flowsQ.data?.results.length ?? 0).toLocaleString()} of {(flowsQ.data?.count ?? 0).toLocaleString()} flows
          {!flowsQ.isLoading && (flowsQ.data?.count ?? 0) === 0 && (
            <span className="ml-2">— this device may not be configured as a NetFlow/sFlow exporter.</span>
          )}
        </div>
      </div>
    </div>
  )
}

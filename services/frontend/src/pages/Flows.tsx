import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import clsx from 'clsx'
import StatCard from '../components/StatCard'
import FlowsTable from '../components/FlowsTable'
import {
  fetchFlows,
  fetchFlowSummary,
  fetchTopTalkers,
  searchFlows,
  type FlowProtocol,
} from '../api/client'
import { fmtBytes } from '../lib/bytes'

const WINDOWS = ['1h', '6h', '24h', '7d'] as const
type Window = (typeof WINDOWS)[number]

const PIE_COLORS = ['#3b82f6', '#a855f7', '#f59e0b', '#22c55e', '#ef4444', '#14b8a6', '#ec4899', '#64748b']

function protocolDonutOption(protocols: FlowProtocol[]): EChartsOption {
  return {
    color: PIE_COLORS,
    tooltip: { trigger: 'item', formatter: '{b}: {c} flows ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [
      {
        name: 'Protocol',
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: 'transparent', borderWidth: 2 },
        label: { show: false },
        data: protocols.map((p) => ({ name: p.protocol, value: p.flows })),
      },
    ],
  }
}

export default function Flows() {
  const [window, setWindow] = useState<Window>('1h')
  // When set, the recent-flows table drills into a single IP (src OR dst).
  const [ipFilter, setIpFilter] = useState<string | null>(null)

  const summaryQ = useQuery({
    queryKey: ['flow-summary', window],
    queryFn: () => fetchFlowSummary({ window }),
  })
  const talkersQ = useQuery({
    queryKey: ['flow-top-talkers', window],
    queryFn: () => fetchTopTalkers({ window, by: 'bytes', limit: '10' }),
  })
  const flowsQ = useQuery({
    queryKey: ['flows', window, ipFilter],
    queryFn: () =>
      ipFilter ? searchFlows(ipFilter, window, 200) : fetchFlows({ window, limit: '200' }),
  })

  const summary = summaryQ.data
  const talkers = talkersQ.data?.results ?? []
  const protocols = summary?.top_protocols ?? []
  const topProto = protocols[0]?.protocol ?? '—'

  return (
    <div className="space-y-6">
      {/* Header + window selector */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Flow Analytics</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            NetFlow / sFlow traffic — top talkers, protocols, and recent conversations
          </p>
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={clsx(
                'px-3 py-1 text-sm rounded-md border',
                window === w
                  ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                  : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800',
              )}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total Flows" value={(summary?.total_flows ?? 0).toLocaleString()} subtitle={`last ${window}`} color="blue" />
        <StatCard title="Total Bytes" value={fmtBytes(summary?.total_bytes ?? 0)} subtitle={`${(summary?.total_packets ?? 0).toLocaleString()} packets`} color="green" />
        <StatCard title="Unique IPs" value={((summary?.unique_src_ips ?? 0) + (summary?.unique_dst_ips ?? 0)).toLocaleString()} subtitle={`${summary?.unique_src_ips ?? 0} src · ${summary?.unique_dst_ips ?? 0} dst`} color="yellow" />
        <StatCard title="Top Protocol" value={topProto} subtitle={protocols[0] ? `${protocols[0].flows.toLocaleString()} flows` : 'no data'} color="blue" />
      </div>

      {/* Two panels: top talkers + protocol distribution */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Panel A: Top Talkers by Bytes */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="font-semibold text-gray-800 dark:text-gray-100">Top Talkers by Bytes</h2>
          </div>
          {talkersQ.isLoading ? (
            <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
          ) : talkers.length === 0 ? (
            <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-500">No flow data in this window.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                    <th className="px-5 py-2.5 font-medium">Source IP</th>
                    <th className="px-5 py-2.5 font-medium text-right">Bytes</th>
                    <th className="px-5 py-2.5 font-medium text-right">Flows</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {talkers.map((t) => (
                    <tr key={t.src_ip} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                      <td className="px-5 py-2">
                        <button onClick={() => setIpFilter(t.src_ip)} className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline">
                          {t.src_ip}
                        </button>
                      </td>
                      <td className="px-5 py-2 text-right font-mono text-xs text-gray-700 dark:text-gray-300">{fmtBytes(t.bytes)}</td>
                      <td className="px-5 py-2 text-right font-mono text-xs text-gray-600 dark:text-gray-400">{t.flows.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Panel B: Protocol Distribution */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <h2 className="font-semibold text-gray-800 dark:text-gray-100 mb-1">Protocol Distribution</h2>
          {protocols.length === 0 ? (
            <p className="py-16 text-center text-sm text-gray-400 dark:text-gray-500">No flow data in this window.</p>
          ) : (
            <ReactECharts option={protocolDonutOption(protocols)} style={{ height: 260 }} opts={{ renderer: 'svg' }} notMerge />
          )}
        </div>
      </div>

      {/* Recent flows */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-2">
          <h2 className="font-semibold text-gray-800 dark:text-gray-100">Recent Flows</h2>
          {ipFilter && (
            <span className="flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-gray-700 rounded-full px-3 py-1">
              Filtering: <span className="font-mono">{ipFilter}</span>
              <button onClick={() => setIpFilter(null)} className="font-medium underline hover:no-underline">clear</button>
            </span>
          )}
        </div>
        <FlowsTable rows={flowsQ.data?.results ?? []} loading={flowsQ.isLoading} onIpClick={setIpFilter} />
        <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
          {(flowsQ.data?.results.length ?? 0).toLocaleString()} of {(flowsQ.data?.count ?? 0).toLocaleString()} flows
        </div>
      </div>
    </div>
  )
}

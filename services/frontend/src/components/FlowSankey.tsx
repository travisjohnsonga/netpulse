import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import { fmtBytes } from '../lib/bytes'
import { useIsDark, chartColors } from '../lib/useIsDark'
import type { FlowSankeyData, FlowSankeyLink } from '../api/client'

// RFC1918 private ranges: 10/8, 172.16/12, 192.168/16.
function isPrivate(ip: string): boolean {
  if (ip.startsWith('10.') || ip.startsWith('192.168.')) return true
  const m = ip.match(/^172\.(\d+)\./)
  return m ? Number(m[1]) >= 16 && Number(m[1]) <= 31 : false
}

// Device own IP → green, internal (RFC1918) → blue, external → amber.
function nodeColor(ip: string, deviceIp?: string | null): string {
  if (deviceIp && ip === deviceIp) return '#10b981'
  return isPrivate(ip) ? '#3b82f6' : '#f59e0b'
}

// ECharts Sankey requires a DAG. Links arrive byte-sorted (desc); keep each one
// unless it would close a cycle, so the highest-volume conversations win.
function acyclic(links: FlowSankeyLink[]): FlowSankeyLink[] {
  const adj = new Map<string, Set<string>>()
  const reaches = (from: string, to: string, seen = new Set<string>()): boolean => {
    if (from === to) return true
    if (seen.has(from)) return false
    seen.add(from)
    for (const n of adj.get(from) ?? []) if (reaches(n, to, seen)) return true
    return false
  }
  const kept: FlowSankeyLink[] = []
  for (const l of links) {
    if (l.source === l.target || reaches(l.target, l.source)) continue
    if (!adj.has(l.source)) adj.set(l.source, new Set())
    adj.get(l.source)!.add(l.target)
    kept.push(l)
  }
  return kept
}

function sankeyOption(links: FlowSankeyLink[], deviceIp: string | null | undefined, isDark: boolean): EChartsOption {
  // Derive nodes from the kept links so no orphans float in the diagram.
  const names: string[] = []
  const seen = new Set<string>()
  for (const l of links) for (const ip of [l.source, l.target]) {
    if (!seen.has(ip)) { seen.add(ip); names.push(ip) }
  }
  return {
    tooltip: {
      trigger: 'item',
      triggerOn: 'mousemove',
      formatter: (params: any) => {
        if (params.dataType === 'edge') {
          const d = params.data
          return `${d.source} → ${d.target}<br/>Bytes: ${fmtBytes(d.bytes)}<br/>Packets: ${d.packets.toLocaleString()}<br/>Flows: ${d.flows.toLocaleString()}`
        }
        return params.name
      },
    },
    series: [
      {
        type: 'sankey',
        emphasis: { focus: 'adjacency' },
        nodeAlign: 'left',
        nodeGap: 10,
        data: names.map((name) => ({
          name,
          itemStyle: { color: nodeColor(name, deviceIp), borderWidth: 0 },
        })),
        links,
        label: { color: chartColors(isDark).text, fontSize: 11, fontWeight: 500 },
        lineStyle: { color: 'gradient', opacity: isDark ? 0.4 : 0.5 },
      },
    ],
  }
}

export default function FlowSankey({
  data,
  loading,
  deviceIp,
  height = 320,
}: {
  data: FlowSankeyData | undefined
  loading: boolean
  deviceIp?: string | null
  height?: number
}) {
  const isDark = useIsDark()
  const links = useMemo(() => acyclic(data?.links ?? []), [data])

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
        <h2 className="font-semibold text-gray-800 dark:text-gray-100">Traffic Flow</h2>
      </div>
      {loading ? (
        <div className="flex items-center justify-center" style={{ height }}>
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : links.length === 0 ? (
        <p className="px-5 text-center text-sm text-gray-400 dark:text-gray-500 flex items-center justify-center" style={{ height }}>
          No flow data yet — configure NetFlow/sFlow export to this collector
        </p>
      ) : (
        <>
          <ReactECharts
            option={sankeyOption(links, deviceIp, isDark)}
            style={{ height }}
            opts={{ renderer: 'svg' }}
            notMerge
          />
          <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
            Showing top {links.length} conversation{links.length === 1 ? '' : 's'} by bytes
          </div>
        </>
      )}
    </div>
  )
}

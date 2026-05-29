import { useEffect, useRef, useState } from 'react'
import cytoscape, { type Core, type NodeSingular, type EdgeSingular } from 'cytoscape'
import { fetchTopology, type TopologyNode, type TopologyEdge } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'

type Popup =
  | { kind: 'node'; data: TopologyNode; x: number; y: number }
  | { kind: 'edge'; data: TopologyEdge; x: number; y: number }
  | null

const STATUS_COLORS: Record<string, string> = {
  active: '#22c55e',
  inactive: '#6b7280',
  pending: '#f59e0b',
  unreachable: '#ef4444',
}

const UTILIZATION_COLORS: Record<string, string> = {
  green: '#22c55e',
  yellow: '#eab308',
  orange: '#f97316',
  red: '#ef4444',
  gray: '#9ca3af',
}

function utilColor(pct: number, down: boolean): string {
  if (down) return UTILIZATION_COLORS.gray
  if (pct >= 90) return UTILIZATION_COLORS.red
  if (pct >= 80) return UTILIZATION_COLORS.orange
  if (pct >= 60) return UTILIZATION_COLORS.yellow
  return UTILIZATION_COLORS.green
}

function fmtBps(bps: number): string {
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(1)} Gbps`
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(1)} Kbps`
  return `${bps} bps`
}

export default function Topology() {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const [popup, setPopup] = useState<Popup>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nodeCount, setNodeCount] = useState(0)
  const [edgeCount, setEdgeCount] = useState(0)
  const { lastMessage } = useWebSocket('/ws/telemetry/')

  const buildGraph = (nodes: TopologyNode[], edges: TopologyEdge[]) => {
    if (!containerRef.current) return

    cyRef.current?.destroy()

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...nodes.map((n) => ({
          data: {
            id: n.id,
            label: n.label,
            type: n.type,
            status: n.status,
            risk_score: n.risk_score,
            raw: n,
          },
        })),
        ...edges.map((e, i) => ({
          data: {
            id: `e${i}`,
            source: e.source,
            target: e.target,
            utilization_pct: e.utilization_pct,
            capacity_gbps: e.capacity_gbps,
            in_bps: e.in_bps,
            out_bps: e.out_bps,
            latency_ms: e.latency_ms,
            raw: e,
          },
        })),
      ],
      layout: { name: 'cose', animate: false, padding: 40, nodeRepulsion: 6000 },
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (n: NodeSingular) =>
              STATUS_COLORS[n.data('status') as string] ?? '#6b7280',
            label: 'data(label)',
            'font-size': 11,
            color: '#1f2937',
            'text-valign': 'bottom',
            'text-margin-y': 6,
            'text-background-color': '#ffffff',
            'text-background-opacity': 0.8,
            'text-background-padding': '2px',
            width: 36,
            height: 36,
            'border-width': 2,
            'border-color': '#ffffff',
          },
        },
        {
          selector: 'edge',
          style: {
            width: (e: EdgeSingular) => {
              const gbps: number = e.data('capacity_gbps') as number
              if (gbps >= 100) return 6
              if (gbps >= 40) return 5
              if (gbps >= 10) return 4
              return 2
            },
            'line-color': (e: EdgeSingular) =>
              utilColor(e.data('utilization_pct') as number, false),
            'curve-style': 'bezier',
            'target-arrow-shape': 'none',
          },
        },
        {
          selector: 'node:selected',
          style: { 'border-color': '#3b82f6', 'border-width': 3 },
        },
        {
          selector: 'edge:selected',
          style: { 'line-color': '#3b82f6', width: 4 },
        },
      ],
    })

    cy.on('tap', 'node', (evt) => {
      const node = evt.target as NodeSingular
      const pos = evt.renderedPosition
      setPopup({ kind: 'node', data: node.data('raw') as TopologyNode, x: pos.x, y: pos.y })
    })

    cy.on('tap', 'edge', (evt) => {
      const edge = evt.target as EdgeSingular
      const pos = evt.renderedPosition
      setPopup({ kind: 'edge', data: edge.data('raw') as TopologyEdge, x: pos.x, y: pos.y })
    })

    cy.on('tap', (evt) => {
      if (evt.target === cy) setPopup(null)
    })

    cyRef.current = cy
    setNodeCount(nodes.length)
    setEdgeCount(edges.length)
  }

  useEffect(() => {
    setLoading(true)
    fetchTopology()
      .then(({ nodes, edges }) => {
        buildGraph(nodes, edges)
        setLoading(false)
      })
      .catch(() => {
        setError('Could not load topology. Check that the API is running.')
        setLoading(false)
      })
    return () => { cyRef.current?.destroy() }
  }, [])

  // Handle live utilization updates from WebSocket
  useEffect(() => {
    if (!lastMessage || !cyRef.current) return
    const msg = lastMessage as { type?: string; utilization?: Record<string, number> }
    if (msg.type !== 'topology_update' || !msg.utilization) return
    const cy = cyRef.current
    cy.edges().forEach((edge) => {
      const id: string = edge.data('id') as string
      if (id in msg.utilization!) {
        const pct = msg.utilization![id]
        edge.data('utilization_pct', pct)
        edge.style('line-color', utilColor(pct, false))
      }
    })
  }, [lastMessage])

  const fitView = () => cyRef.current?.fit(undefined, 40)
  const resetZoom = () => cyRef.current?.reset()

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Network Topology</h1>
          {!loading && !error && (
            <p className="text-sm text-gray-500 mt-0.5">
              {nodeCount} devices &middot; {edgeCount} links
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={fitView}
            className="px-3 py-1.5 border border-gray-300 text-sm text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Fit
          </button>
          <button
            onClick={resetZoom}
            className="px-3 py-1.5 border border-gray-300 text-sm text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs flex-shrink-0">
        <div className="flex items-center gap-4 bg-white rounded-lg border border-gray-200 px-4 py-2">
          <span className="font-medium text-gray-600">Node status:</span>
          {Object.entries(STATUS_COLORS).map(([k, c]) => (
            <span key={k} className="flex items-center gap-1.5 capitalize text-gray-600">
              <span className="w-3 h-3 rounded-full" style={{ background: c }} />
              {k}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-4 bg-white rounded-lg border border-gray-200 px-4 py-2">
          <span className="font-medium text-gray-600">Link utilization:</span>
          {[['<60%', 'green'], ['60-80%', 'yellow'], ['80-90%', 'orange'], ['>90%', 'red'], ['down', 'gray']].map(([label, color]) => (
            <span key={label} className="flex items-center gap-1.5 text-gray-600">
              <span className="w-6 h-1.5 rounded" style={{ background: UTILIZATION_COLORS[color] }} />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Graph canvas */}
      <div className="relative flex-1 bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden" style={{ minHeight: 480 }}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10">
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-gray-500">Loading topology…</span>
            </div>
          </div>
        )}

        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10">
            <div className="text-center">
              <p className="text-5xl mb-4">🗺</p>
              <p className="text-lg font-semibold text-gray-700 mb-2">No topology data</p>
              <p className="text-sm text-gray-500 max-w-xs">{error}</p>
            </div>
          </div>
        )}

        {!loading && !error && nodeCount === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10">
            <div className="text-center">
              <p className="text-5xl mb-4">🌐</p>
              <p className="text-lg font-semibold text-gray-700 mb-2">No devices yet</p>
              <p className="text-sm text-gray-500">Add devices and CDP/LLDP data will build the topology automatically.</p>
            </div>
          </div>
        )}

        <div ref={containerRef} className="w-full h-full" />

        {/* Popup overlay */}
        {popup && (
          <div
            className="absolute z-20 bg-white rounded-xl shadow-xl border border-gray-200 p-4 w-64 text-sm"
            style={{ left: Math.min(popup.x + 12, window.innerWidth - 280), top: popup.y + 12 }}
          >
            <button
              className="absolute top-2 right-2 text-gray-400 hover:text-gray-600 text-lg leading-none"
              onClick={() => setPopup(null)}
            >
              ×
            </button>

            {popup.kind === 'node' ? (
              <>
                <p className="font-semibold text-gray-900 pr-4">{popup.data.label}</p>
                <p className="text-gray-500 text-xs mt-0.5 mb-3">{popup.data.type}</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Status</span>
                    <span className="capitalize font-medium" style={{ color: STATUS_COLORS[popup.data.status] ?? '#6b7280' }}>
                      {popup.data.status}
                    </span>
                  </div>
                  {popup.data.site && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Site</span>
                      <span className="text-gray-800">{popup.data.site}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">Risk score</span>
                    <span className={`font-medium ${popup.data.risk_score > 70 ? 'text-red-600' : popup.data.risk_score > 40 ? 'text-yellow-600' : 'text-green-600'}`}>
                      {popup.data.risk_score}
                    </span>
                  </div>
                </div>
              </>
            ) : (
              <>
                <p className="font-semibold text-gray-900 mb-3">Link Details</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Capacity</span>
                    <span className="text-gray-800">{popup.data.capacity_gbps} Gbps</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Utilization</span>
                    <span
                      className="font-medium"
                      style={{ color: utilColor(popup.data.utilization_pct, false) }}
                    >
                      {popup.data.utilization_pct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">In</span>
                    <span className="text-gray-800 font-mono text-xs">{fmtBps(popup.data.in_bps)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Out</span>
                    <span className="text-gray-800 font-mono text-xs">{fmtBps(popup.data.out_bps)}</span>
                  </div>
                  {popup.data.latency_ms !== null && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Latency</span>
                      <span className="text-gray-800">{popup.data.latency_ms} ms</span>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

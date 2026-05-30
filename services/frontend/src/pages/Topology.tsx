import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import cytoscape, { type Core, type NodeSingular, type EdgeSingular } from 'cytoscape'
import {
  fetchTopology, fetchDevices, fetchSites, discoverDeviceLinks,
  type TopologyNode, type TopologyEdge, type Device, type Site,
} from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'

type Popup =
  | { kind: 'node'; data: TopologyNode; x: number; y: number }
  | { kind: 'edge'; data: TopologyEdge; x: number; y: number }
  | null

const STATUS_COLORS: Record<string, string> = {
  active: '#22c55e', inactive: '#6b7280', maintenance: '#f59e0b',
  decommissioned: '#ef4444', pending: '#f59e0b', unreachable: '#ef4444',
}
const UTILIZATION_COLORS: Record<string, string> = {
  green: '#22c55e', yellow: '#eab308', orange: '#f97316', red: '#ef4444', gray: '#9ca3af',
}
const ROLES = ['access', 'distribution', 'core', 'wan-edge', 'firewall']
const selCls = 'px-3 py-1.5 text-sm border border-gray-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500'

function typeIcon(type: string): string {
  const t = (type || '').toLowerCase()
  if (t.includes('asa') || t.includes('fortios') || t.includes('panos')) return '🛡'
  if (t === 'nxos' || t === 'eos') return '🔲'
  if (t.includes('ap') || t.includes('wifi')) return '📶'
  return '🔀'  // routers / default
}
function speedWidth(mbps: number | null): number {
  if (!mbps) return 2
  if (mbps >= 40000) return 6
  if (mbps >= 10000) return 4
  if (mbps >= 1000) return 3
  return 2
}
function fmtSpeed(mbps: number | null): string {
  if (!mbps) return '—'
  return mbps >= 1000 ? `${mbps / 1000}G` : `${mbps}M`
}

export default function Topology() {
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const [popup, setPopup] = useState<Popup>(null)
  const [hover, setHover] = useState<{ x: number; y: number; title: string; lines: string[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nodeCount, setNodeCount] = useState(0)
  const [edgeCount, setEdgeCount] = useState(0)
  const { lastMessage } = useWebSocket('/ws/telemetry/')

  // Filters
  const [devices, setDevices] = useState<Device[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [site, setSite] = useState('')
  const [center, setCenter] = useState('')
  const [depth, setDepth] = useState('all')
  const [role, setRole] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])

  const buildGraph = (nodes: TopologyNode[], edges: TopologyEdge[]) => {
    if (!containerRef.current) return
    cyRef.current?.destroy()

    const labelById: Record<string, string> = Object.fromEntries(nodes.map((n) => [n.id, n.label]))
    // Group edges by unordered device pair to detect parallel links.
    const pairKey = (e: TopologyEdge) => [e.source, e.target].sort().join('|')
    const groups: Record<string, TopologyEdge[]> = {}
    for (const e of edges) (groups[pairKey(e)] ||= []).push(e)

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...nodes.map((n) => ({ data: { id: n.id, label: `${typeIcon(n.type)} ${n.label}`, status: n.status, raw: n } })),
        ...edges.map((e, i) => {
          const group = groups[pairKey(e)]
          const count = group.length
          // Show the parallel-link count once, on the first edge of the group.
          const isFirst = group[0] === e
          return { data: {
            id: `e${i}`, source: e.source, target: e.target,
            ports: `${e.port_a} ↔ ${e.port_b}`, width: speedWidth(e.speed_mbps),
            color: UTILIZATION_COLORS[e.utilization_color] ?? UTILIZATION_COLORS.green,
            countLabel: count > 1 && isFirst ? `×${count}` : '',
            raw: e,
          } }
        }),
      ],
      layout: { name: 'cose', animate: false, padding: 40, nodeRepulsion: 6000 },
      style: [
        { selector: 'node', style: {
          'background-color': (n: NodeSingular) => STATUS_COLORS[n.data('status') as string] ?? '#6b7280',
          label: 'data(label)', 'font-size': 11, color: '#1f2937',
          'text-valign': 'bottom', 'text-margin-y': 5,
          'text-background-color': '#ffffff', 'text-background-opacity': 0.85, 'text-background-padding': '2px',
          width: 45, height: 45, 'border-width': 2, 'border-color': '#ffffff',
        } },
        { selector: 'edge', style: {
          width: 'data(width)', 'line-color': 'data(color)', 'curve-style': 'bezier', 'target-arrow-shape': 'none',
          label: 'data(countLabel)', 'font-size': 11, 'font-weight': 700, color: '#374151',
          'text-background-color': '#ffffff', 'text-background-opacity': 0.9, 'text-background-padding': '2px',
        } },
        { selector: 'node:selected', style: { 'border-color': '#3b82f6', 'border-width': 3 } },
        { selector: 'edge:selected', style: { 'line-color': '#3b82f6' } },
        { selector: 'edge.hover', style: { 'line-color': '#3b82f6' } },
      ],
    })

    const edgeTooltip = (e: TopologyEdge): string[] => {
      const g = groups[pairKey(e)]
      const s = labelById[e.source] ?? e.source
      const t = labelById[e.target] ?? e.target
      return g.map((x) => `${s}:${x.port_a} ↔ ${t}:${x.port_b}`)
    }

    cy.on('tap', 'node', (ev) => { const p = ev.renderedPosition; setPopup({ kind: 'node', data: (ev.target as NodeSingular).data('raw') as TopologyNode, x: p.x, y: p.y }) })
    cy.on('tap', 'edge', (ev) => { const p = ev.renderedPosition; setPopup({ kind: 'edge', data: (ev.target as EdgeSingular).data('raw') as TopologyEdge, x: p.x, y: p.y }) })
    cy.on('mouseover', 'node', (ev) => {
      const n = (ev.target as NodeSingular).data('raw') as TopologyNode
      const p = ev.renderedPosition
      setHover({ x: p.x, y: p.y, title: n.label, lines: [
        `Platform: ${n.type || '—'}`, `Vendor: ${n.vendor || '—'}`,
        `Status: ${n.status}`, `Site: ${n.site || '—'}`, `IP: ${n.ip || '—'}`,
      ] })
    })
    cy.on('mouseover', 'edge', (ev) => {
      const e = (ev.target as EdgeSingular).data('raw') as TopologyEdge
      const p = ev.renderedPosition
      ;(ev.target as EdgeSingular).addClass('hover')
      setHover({ x: p.x, y: p.y, title: 'Link', lines: edgeTooltip(e) })
    })
    cy.on('mouseout', 'node edge', (ev) => { (ev.target as EdgeSingular).removeClass('hover'); setHover(null) })
    cy.on('pan zoom drag', () => setHover(null))
    cy.on('tap', (ev) => { if (ev.target === cy) setPopup(null) })
    cyRef.current = cy
    setNodeCount(nodes.length); setEdgeCount(edges.length)
  }

  const reload = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = {}
    if (site) params.site = site
    if (center) params.device = center
    if (depth && depth !== 'all') params.depth = depth
    if (role) params.role = role
    fetchTopology(params)
      .then(({ nodes, edges }) => { buildGraph(nodes, edges); setError(null) })
      .catch(() => setError('Could not load topology. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [site, center, depth, role])

  useEffect(() => { reload(); return () => { cyRef.current?.destroy() } }, [reload])

  useEffect(() => {
    if (!lastMessage || !cyRef.current) return
    const msg = lastMessage as { type?: string; utilization?: Record<string, number> }
    if (msg.type !== 'topology_update' || !msg.utilization) return
    cyRef.current.edges().forEach((edge) => {
      const id = edge.data('id') as string
      if (id in msg.utilization!) edge.data('color', UTILIZATION_COLORS.green)
    })
  }, [lastMessage])

  const discoverAll = async () => {
    setDiscovering(true); setToast(null)
    let links = 0, devs = 0
    const targets = center ? devices.filter((d) => String(d.id) === center) : devices
    for (const d of targets) {
      try { const r = await discoverDeviceLinks(d.id); if (r.matched > 0) { links += r.matched; devs += 1 } } catch { /* skip */ }
    }
    setToast(`Found ${links} link${links !== 1 ? 's' : ''} across ${devs} device${devs !== 1 ? 's' : ''}`)
    setDiscovering(false)
    reload()
  }

  const fitView = () => cyRef.current?.fit(undefined, 40)
  const resetZoom = () => cyRef.current?.reset()

  return (
    <div className="flex flex-col h-full space-y-3">
      <div className="flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Network Topology</h1>
          {!loading && !error && <p className="text-sm text-gray-500 mt-0.5">{nodeCount} devices · {edgeCount} links</p>}
        </div>
        <div className="flex gap-2">
          <button onClick={fitView} className="px-3 py-1.5 border border-gray-300 text-sm text-gray-700 rounded-lg hover:bg-gray-50">Fit</button>
          <button onClick={resetZoom} className="px-3 py-1.5 border border-gray-300 text-sm text-gray-700 rounded-lg hover:bg-gray-50">Reset</button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 bg-white rounded-lg border border-gray-200 p-3 flex-shrink-0">
        <select className={selCls} value={site} onChange={(e) => setSite(e.target.value)}>
          <option value="">All Sites</option>
          {sites.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
        </select>
        <select className={selCls} value={center} onChange={(e) => setCenter(e.target.value)}>
          <option value="">All Devices (center)</option>
          {devices.map((d) => <option key={d.id} value={String(d.id)}>{d.hostname}</option>)}
        </select>
        <select className={selCls} value={depth} onChange={(e) => setDepth(e.target.value)} disabled={!center} title={!center ? 'Pick a center device' : ''}>
          <option value="all">All hops</option>
          <option value="1">1 hop</option>
          <option value="2">2 hops</option>
          <option value="3">3 hops</option>
        </select>
        <select className={selCls} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="">All Roles</option>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button onClick={discoverAll} disabled={discovering} className="ml-auto px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
          {discovering ? 'Discovering…' : '🔍 Discover Links'}
        </button>
      </div>
      {toast && <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm text-green-800 flex-shrink-0">{toast}</div>}

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs flex-shrink-0">
        <div className="flex items-center gap-3 bg-white rounded-lg border border-gray-200 px-4 py-2">
          <span className="font-medium text-gray-600">Status:</span>
          {Object.entries(STATUS_COLORS).slice(0, 4).map(([k, c]) => (
            <span key={k} className="flex items-center gap-1.5 capitalize text-gray-600"><span className="w-3 h-3 rounded-full" style={{ background: c }} />{k}</span>
          ))}
        </div>
        <div className="flex items-center gap-3 bg-white rounded-lg border border-gray-200 px-4 py-2">
          <span className="font-medium text-gray-600">Link util:</span>
          {[['<60%', 'green'], ['60-80%', 'yellow'], ['80-90%', 'orange'], ['>90%', 'red'], ['down', 'gray']].map(([label, color]) => (
            <span key={label} className="flex items-center gap-1.5 text-gray-600"><span className="w-6 h-1.5 rounded" style={{ background: UTILIZATION_COLORS[color] }} />{label}</span>
          ))}
        </div>
      </div>

      {/* Graph */}
      <div className="relative flex-1 bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden" style={{ minHeight: 460 }}>
        {loading && <div className="absolute inset-0 flex items-center justify-center bg-white z-10"><div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>}
        {error && <div className="absolute inset-0 flex items-center justify-center bg-white z-10 text-center"><div><p className="text-5xl mb-3">🗺</p><p className="text-sm text-gray-500 max-w-xs">{error}</p></div></div>}
        {!loading && !error && nodeCount === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10 text-center">
            <div><p className="text-5xl mb-3">🌐</p><p className="text-lg font-semibold text-gray-700 mb-1">No devices match</p>
              <p className="text-sm text-gray-500">Adjust filters, or click Discover Links to map LLDP neighbors.</p></div>
          </div>
        )}
        <div ref={containerRef} className="w-full h-full" />

        {/* Hover tooltip (informational; click a node/link for actions) */}
        {hover && !popup && (
          <div
            className="absolute z-30 pointer-events-none bg-gray-900 text-white text-xs rounded-lg shadow-lg px-3 py-2 max-w-xs"
            style={{ left: Math.min(hover.x + 14, (containerRef.current?.clientWidth ?? 600) - 240), top: hover.y + 14 }}
          >
            <p className="font-semibold mb-0.5">{hover.title}</p>
            {hover.lines.map((l, i) => <p key={i} className="text-gray-200 font-mono leading-snug">{l}</p>)}
          </div>
        )}

        {popup && (
          <div className="absolute z-20 bg-white rounded-xl shadow-xl border border-gray-200 p-4 w-64 text-sm"
            style={{ left: Math.min(popup.x + 12, (containerRef.current?.clientWidth ?? 600) - 270), top: popup.y + 12 }}>
            <button className="absolute top-2 right-2 text-gray-400 hover:text-gray-600 text-lg leading-none" onClick={() => setPopup(null)}>×</button>
            {popup.kind === 'node' ? (
              <>
                <p className="font-semibold text-gray-900 pr-4">{popup.data.label}</p>
                <p className="text-gray-500 text-xs mt-0.5 mb-3">{popup.data.type}{popup.data.role ? ` · ${popup.data.role}` : ''}</p>
                <div className="space-y-1.5">
                  <Row k="Status" v={<span className="capitalize font-medium" style={{ color: STATUS_COLORS[popup.data.status] ?? '#6b7280' }}>{popup.data.status}</span>} />
                  {popup.data.vendor && <Row k="Vendor" v={popup.data.vendor} />}
                  {popup.data.ip && <Row k="IP" v={<span className="font-mono text-xs">{popup.data.ip}</span>} />}
                  {popup.data.site && <Row k="Site" v={popup.data.site} />}
                  <Row k="Risk score" v={popup.data.risk_score} />
                </div>
                <button onClick={() => navigate(`/devices/${popup.data.id}`)} className="mt-3 w-full py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-md font-medium">View Device →</button>
              </>
            ) : (
              <>
                <p className="font-semibold text-gray-900 mb-2">Link</p>
                <p className="text-xs font-mono text-gray-700 mb-3">{popup.data.port_a} ↔ {popup.data.port_b}</p>
                <div className="space-y-1.5">
                  <Row k="Speed" v={fmtSpeed(popup.data.speed_mbps)} />
                  <Row k="Utilization" v={`${popup.data.utilization_pct}%`} />
                </div>
                <button onClick={() => navigate(`/devices/${popup.data.source}`)} className="mt-3 w-full py-1.5 text-xs border border-gray-300 rounded-md font-medium hover:bg-gray-50">View Interface →</button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between"><span className="text-gray-500">{k}</span><span className="text-gray-800">{v}</span></div>
}

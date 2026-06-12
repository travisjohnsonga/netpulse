import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import cytoscape, { type Core, type NodeSingular, type EdgeSingular } from 'cytoscape'
import {
  fetchTopology, fetchDevices, discoverDeviceLinks,
  type TopologyNode, type TopologyEdge, type Device,
} from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import { useSite } from '../store/siteStore'

type Popup =
  | { kind: 'node'; data: TopologyNode; x: number; y: number }
  | { kind: 'edge'; data: TopologyEdge; x: number; y: number }
  | null

const OFFLINE_COLOR = '#e74c3c'
const DEFAULT_ROLE_COLOR = '#607D8B'

// Visual style + layout tier per (normalised) device role. Tier drives the
// hierarchical layout: routers/firewalls top, then core, distribution, access,
// APs at the bottom.
interface RoleStyle { shape: string; color: string; size: number; icon: string; tier: number }
const NODE_STYLES: Record<string, RoleStyle> = {
  router:        { shape: 'ellipse',         color: '#FF9800', size: 46, icon: '🔀', tier: 0 },
  firewall:      { shape: 'round-triangle',  color: '#f44336', size: 46, icon: '🛡', tier: 0 },
  'core-switch': { shape: 'diamond',         color: '#2196F3', size: 52, icon: '◆', tier: 1 },
  distribution:  { shape: 'hexagon',         color: '#00BCD4', size: 46, icon: '⬡', tier: 2 },
  'access-switch':{ shape: 'round-rectangle', color: '#4CAF50', size: 40, icon: '▪', tier: 3 },
  'wireless-ap': { shape: 'ellipse',         color: '#9C27B0', size: 34, icon: '📶', tier: 4 },
  default:       { shape: 'ellipse',         color: DEFAULT_ROLE_COLOR, size: 38, icon: '●', tier: 2 },
}

// Map a node's role slug / role name / platform to one of the NODE_STYLES keys.
function roleKey(n: TopologyNode): string {
  const r = `${n.role_slug || ''} ${n.role || ''}`.toLowerCase()
  const t = (n.type || '').toLowerCase()
  if (t === 'unifi_ap' || r.includes('wireless') || /\bap\b/.test(r)) return 'wireless-ap'
  if (r.includes('firewall') || t.includes('fortios') || t.includes('panos') || t.includes('sonicwall') || t.includes('asa')) return 'firewall'
  // UniFi consoles/gateways (UDM, Cloud Key, UXG, USG) sit at the gateway tier.
  if (t === 'unifi_udm' || t === 'unifi_gw' || t === 'unifi_ucg' || t === 'unifi_uckp') return 'router'
  if (r.includes('core')) return 'core-switch'
  if (r.includes('distrib')) return 'distribution'
  if (r.includes('access') || t === 'aos_cx' || t === 'nxos' || t === 'eos' || t === 'unifi_sw') return 'access-switch'
  if (r.includes('router') || r.includes('wan') || t.startsWith('ios') || t === 'junos') return 'router'
  return 'default'
}
function styleFor(n: TopologyNode): RoleStyle {
  return NODE_STYLES[roleKey(n)] ?? NODE_STYLES.default
}

function isOffline(n: TopologyNode): boolean {
  return n.is_reachable === false || n.status === 'inactive' || n.status === 'unreachable'
}
function isAP(n: TopologyNode): boolean {
  return roleKey(n) === 'wireless-ap'
}
function nodeColor(n: TopologyNode): string {
  return isOffline(n) ? OFFLINE_COLOR : styleFor(n).color
}
// Edge colour by the up/down state of its two endpoints.
const EDGE_UP = '#4CAF50', EDGE_PARTIAL = '#FF9800', EDGE_DOWN = '#e74c3c', EDGE_UNKNOWN = '#9E9E9E'
function edgeColor(aOffline: boolean | undefined, bOffline: boolean | undefined): string {
  if (aOffline === undefined || bOffline === undefined) return EDGE_UNKNOWN
  if (aOffline && bOffline) return EDGE_DOWN
  if (aOffline || bOffline) return EDGE_PARTIAL
  return EDGE_UP
}
function edgeWidth(linkCount: number): number {
  if (linkCount >= 3) return 5
  if (linkCount === 2) return 3.5
  return 2
}

function relativeTime(iso?: string | null): string {
  if (!iso) return 'never'
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}
function fmtSpeed(mbps: number | null | undefined): string {
  if (!mbps) return '—'
  return mbps >= 1000 ? `${mbps / 1000}G` : `${mbps}M`
}
function apRadioSummary(n: TopologyNode): string {
  if (!n.radios || n.radios.length === 0) return ''
  return n.radios.filter((r) => r.channel != null).map((r) => `${r.band} ch${r.channel}`).join(' / ')
}

// Group-aware hierarchical layout. Upper tiers (routers/core/distribution/
// access) are spread evenly across their row, ordered by their parent's x so
// each group stays in the same left-right order as its upstream. The deepest
// tier (APs) is clustered tightly *under* its parent switch — so each AP sits
// directly below the switch it uplinks to and lines don't cross between groups.
function buildHierarchyLayout(
  nodes: TopologyNode[],
  edges: TopologyEdge[],
  tierOf: (n: TopologyNode) => number,
): Record<string, { x: number; y: number }> {
  const CANVAS_W = 1400
  const TIER_Y: Record<number, number> = { 0: 80, 1: 240, 2: 400, 3: 560, 4: 740 }
  const yFor = (t: number) => TIER_Y[t] ?? 80 + t * 160

  const byId: Record<string, TopologyNode> = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const tier: Record<string, number> = Object.fromEntries(nodes.map((n) => [n.id, tierOf(n)]))

  // Orient each edge parent→child by tier (lower tier number = upstream). Each
  // child keeps a single primary parent (first seen); same-tier links are peers.
  const parent = new Map<string, string>()
  for (const e of edges) {
    const a = e.source, b = e.target
    if (!(a in tier) || !(b in tier) || tier[a] === tier[b]) continue
    const [p, c] = tier[a] < tier[b] ? [a, b] : [b, a]
    if (!parent.has(c)) parent.set(c, p)
  }

  const cmpLabel = (x: string, y: string) => (byId[x]?.label || '').localeCompare(byId[y]?.label || '')
  const tiers: Record<number, TopologyNode[]> = {}
  nodes.forEach((n) => { (tiers[tier[n.id]] ||= []).push(n) })
  const tierKeys = Object.keys(tiers).map(Number).sort((a, b) => a - b)
  const apTier = tierKeys.length ? tierKeys[tierKeys.length - 1] : 0

  const pos: Record<string, { x: number; y: number }> = {}
  const spreadEven = (arr: TopologyNode[], y: number, yOffset = 0) =>
    arr.forEach((n, i) => { pos[n.id] = { x: (CANVAS_W / (arr.length + 1)) * (i + 1), y: y + yOffset } })

  // Upper tiers, top-down: order by parent x (already placed), then spread evenly.
  for (const t of tierKeys) {
    if (t === apTier) continue
    const arr = [...tiers[t]].sort((a, b) => {
      const pa = parent.get(a.id), pb = parent.get(b.id)
      const xa = pa && pos[pa] ? pos[pa].x : 0
      const xb = pb && pos[pb] ? pos[pb].x : 0
      return (xa - xb) || cmpLabel(a.id, b.id)
    })
    spreadEven(arr, yFor(t))
  }

  // Deepest tier (APs): cluster each group centred under its parent switch.
  const groups = new Map<string, TopologyNode[]>()
  const orphans: TopologyNode[] = []
  for (const ap of tiers[apTier] || []) {
    const p = parent.get(ap.id)
    if (p && pos[p]) {
      if (!groups.has(p)) groups.set(p, [])
      groups.get(p)!.push(ap)
    } else {
      orphans.push(ap)
    }
  }
  groups.forEach((aps, switchId) => {
    const sx = pos[switchId].x
    const sorted = [...aps].sort((a, b) => cmpLabel(a.id, b.id))
    const spacing = Math.min(44, 220 / Math.max(1, sorted.length))
    const startX = sx - (spacing * (sorted.length - 1)) / 2
    sorted.forEach((ap, i) => { pos[ap.id] = { x: startX + spacing * i, y: yFor(apTier) } })
  })
  // Orphan APs (no matched uplink) spread on a row just below their tier.
  spreadEven(orphans.sort((a, b) => cmpLabel(a.id, b.id)), yFor(apTier), 60)

  nodes.forEach((n) => { if (!(n.id in pos)) pos[n.id] = { x: CANVAS_W / 2, y: yFor(tier[n.id]) } })
  return pos
}

const ROLES = ['access', 'distribution', 'core', 'wan-edge', 'firewall']
const selCls = 'px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const btnCls = 'px-3 py-1.5 border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50'

interface MiniState { box: { x: number; y: number; w: number; h: number }; dots: { x: number; y: number; c: string }[]; view: { x: number; y: number; w: number; h: number } }

export default function Topology() {
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const dataRef = useRef<{ nodes: TopologyNode[]; edges: TopologyEdge[] }>({ nodes: [], edges: [] })
  const [popup, setPopup] = useState<Popup>(null)
  const [hover, setHover] = useState<{ x: number; y: number; title: string; lines: string[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nodeCount, setNodeCount] = useState(0)
  const [edgeCount, setEdgeCount] = useState(0)
  const [mini, setMini] = useState<MiniState | null>(null)
  const { lastMessage: deviceMessage } = useWebSocket('/ws/devices/')

  // Filters
  const [devices, setDevices] = useState<Device[]>([])
  // Site scoping comes from the global header selector.
  const { selectedSite: site } = useSite()
  const [center, setCenter] = useState('')
  const [depth, setDepth] = useState('all')
  const [role, setRole] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'online' | 'offline'>('all')
  // View toggles
  const [showLabels, setShowLabels] = useState(true)
  const [showAPs, setShowAPs] = useState(true)
  const [layoutMode, setLayoutMode] = useState<'hier' | 'force'>('hier')
  const [legendOpen, setLegendOpen] = useState(true)
  const [discovering, setDiscovering] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => {})
  }, [])

  const updateMinimap = useCallback(() => {
    const cy = cyRef.current
    if (!cy || cy.nodes().length === 0) { setMini(null); return }
    const bb = cy.elements().boundingBox()
    const W = 150, H = 100, pad = 6
    const sx = (W - pad * 2) / Math.max(1, bb.w), sy = (H - pad * 2) / Math.max(1, bb.h)
    const s = Math.min(sx, sy)
    const toMini = (x: number, y: number) => ({ x: pad + (x - bb.x1) * s, y: pad + (y - bb.y1) * s })
    const dots = cy.nodes().map((n) => {
      const p = n.position(); const m = toMini(p.x, p.y)
      return { x: m.x, y: m.y, c: (n.data('color') as string) || DEFAULT_ROLE_COLOR }
    })
    const ext = cy.extent()
    const v1 = toMini(ext.x1, ext.y1), v2 = toMini(ext.x2, ext.y2)
    setMini({
      box: { x: 0, y: 0, w: W, h: H },
      dots,
      view: { x: v1.x, y: v1.y, w: Math.max(2, v2.x - v1.x), h: Math.max(2, v2.y - v1.y) },
    })
  }, [])

  const buildGraph = useCallback((allNodes: TopologyNode[], allEdges: TopologyEdge[]) => {
    if (!containerRef.current) return
    cyRef.current?.destroy()

    // Apply client-side filters (status + show-APs); drop edges that lose an endpoint.
    let nodes = statusFilter === 'all'
      ? allNodes
      : allNodes.filter((n) => (statusFilter === 'offline' ? isOffline(n) : !isOffline(n)))
    if (!showAPs) nodes = nodes.filter((n) => !isAP(n))
    const keep = new Set(nodes.map((n) => n.id))
    const edges = allEdges.filter((e) => keep.has(e.source) && keep.has(e.target))

    const offlineById: Record<string, boolean> = Object.fromEntries(nodes.map((n) => [n.id, isOffline(n)]))
    const labelById: Record<string, string> = Object.fromEntries(nodes.map((n) => [n.id, n.label]))

    // Hierarchical layout: group children under their upstream parent (preset),
    // else fall back to force-directed (cose).
    const positions = layoutMode === 'hier'
      ? buildHierarchyLayout(nodes, edges, (n) => styleFor(n).tier)
      : {}

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...nodes.map((n) => ({
          data: {
            id: n.id,
            label: `${styleFor(n).icon} ${n.label}${isAP(n) && n.client_count != null ? `\n${n.client_count} clients` : ''}`,
            color: nodeColor(n), shape: styleFor(n).shape, size: styleFor(n).size, raw: n,
          },
          classes: isOffline(n) ? 'offline' : '',
          ...(layoutMode === 'hier' ? { position: positions[n.id] } : {}),
        })),
        ...edges.map((e, i) => ({
          data: {
            id: `e${i}`, source: e.source, target: e.target,
            width: edgeWidth(e.link_count ?? 1),
            color: edgeColor(offlineById[e.source], offlineById[e.target]),
            countLabel: (e.link_count ?? 1) > 1 ? (e.label || `×${e.link_count}`) : '',
            raw: e,
          },
        })),
      ],
      layout: layoutMode === 'hier'
        ? { name: 'preset', padding: 70, fit: true }
        : { name: 'cose', animate: false, padding: 40, nodeRepulsion: 6000 },
      style: [
        { selector: 'node', style: {
          'background-color': 'data(color)',
          label: showLabels ? 'data(label)' : '', 'font-size': 10, color: '#1f2937', 'text-wrap': 'wrap',
          'text-valign': 'bottom', 'text-margin-y': 4, 'text-halign': 'center',
          'text-background-color': '#ffffff', 'text-background-opacity': 0.85, 'text-background-padding': '2px',
          width: 'data(size)', height: 'data(size)', 'border-width': 2, 'border-color': '#ffffff',
        } },
        // Per-role node shapes (cytoscape needs a literal NodeShape, not a data mapper).
        { selector: 'node[shape="diamond"]', style: { shape: 'diamond' } },
        { selector: 'node[shape="hexagon"]', style: { shape: 'hexagon' } },
        { selector: 'node[shape="round-rectangle"]', style: { shape: 'round-rectangle' } },
        { selector: 'node[shape="round-triangle"]', style: { shape: 'round-triangle' } },
        { selector: 'node[shape="ellipse"]', style: { shape: 'ellipse' } },
        { selector: 'node.offline', style: {
          'border-color': OFFLINE_COLOR, 'border-width': 3, 'border-style': 'dashed', opacity: 0.6,
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
      const s = labelById[e.source] ?? e.source
      const t = labelById[e.target] ?? e.target
      const members = e.links && e.links.length ? e.links : [{ port_a: e.port_a, port_b: e.port_b, speed_mbps: e.speed_mbps }]
      return members.map((m) => `${s}:${m.port_a || '?'} ↔ ${t}:${m.port_b || '?'}`)
    }

    cy.on('tap', 'node', (ev) => { const p = ev.renderedPosition; setPopup({ kind: 'node', data: (ev.target as NodeSingular).data('raw') as TopologyNode, x: p.x, y: p.y }) })
    cy.on('tap', 'edge', (ev) => { const p = ev.renderedPosition; setPopup({ kind: 'edge', data: (ev.target as EdgeSingular).data('raw') as TopologyEdge, x: p.x, y: p.y }) })
    cy.on('mouseover', 'node', (ev) => {
      const n = (ev.target as NodeSingular).data('raw') as TopologyNode
      const p = ev.renderedPosition
      const offline = isOffline(n)
      const lines = [
        `Role: ${n.role || roleKey(n)}`,
        `IP: ${n.management_ip || n.ip || '—'}`,
        `Platform: ${n.type || '—'}`,
        offline ? '🔴 Offline' : '✅ Online',
      ]
      if (isAP(n)) {
        if (n.client_count != null) lines.push(`📶 ${n.client_count} clients`)
        const radios = apRadioSummary(n)
        if (radios) lines.push(radios)
      } else {
        lines.push(`Neighbors: ${n.neighbor_count ?? 0}`)
      }
      if (offline) lines.push(`Last seen: ${relativeTime(n.last_seen)}`)
      setHover({ x: p.x, y: p.y, title: n.label, lines })
    })
    cy.on('mouseover', 'edge', (ev) => {
      const e = (ev.target as EdgeSingular).data('raw') as TopologyEdge
      const p = ev.renderedPosition
      ;(ev.target as EdgeSingular).addClass('hover')
      setHover({ x: p.x, y: p.y, title: `Link${(e.link_count ?? 1) > 1 ? ` (${e.link_count} members)` : ''}`, lines: edgeTooltip(e) })
    })
    cy.on('mouseout', 'node edge', (ev) => { (ev.target as EdgeSingular).removeClass('hover'); setHover(null) })
    cy.on('pan zoom drag', () => { setHover(null); updateMinimap() })
    cy.on('tap', (ev) => { if (ev.target === cy) setPopup(null) })
    cy.ready(() => updateMinimap())
    cyRef.current = cy
    setNodeCount(nodes.length); setEdgeCount(edges.length)
  }, [statusFilter, showAPs, showLabels, layoutMode, updateMinimap])

  const reload = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = {}
    if (site) params.site = site
    if (center) params.device = center
    if (depth && depth !== 'all') params.depth = depth
    if (role) params.role = role
    fetchTopology(params)
      .then(({ nodes, edges }) => { dataRef.current = { nodes, edges }; buildGraph(nodes, edges); setError(null) })
      .catch(() => setError('Could not load topology. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [site, center, depth, role, buildGraph])

  useEffect(() => { reload(); return () => { cyRef.current?.destroy() } }, [reload])

  // Client-side view toggles rebuild from cached data without refetching.
  useEffect(() => {
    if (dataRef.current.nodes.length) buildGraph(dataRef.current.nodes, dataRef.current.edges)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, showAPs, showLabels, layoutMode])

  // Discovery/enrichment created new topology links → reload the graph.
  useEffect(() => {
    const msg = deviceMessage as { type?: string } | null
    if (msg?.type === 'topology_updated') reload()
  }, [deviceMessage, reload])

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

  const fitView = () => { cyRef.current?.fit(undefined, 40); updateMinimap() }
  const zoomBy = (f: number) => { const cy = cyRef.current; if (!cy) return; cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: (containerRef.current?.clientWidth ?? 600) / 2, y: (containerRef.current?.clientHeight ?? 400) / 2 } }); updateMinimap() }

  return (
    <div className="flex flex-col h-full space-y-3">
      <div className="flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Network Topology</h1>
          {!loading && !error && <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{nodeCount} devices · {edgeCount} links</p>}
        </div>
        <div className="flex gap-2">
          <button onClick={reload} className={btnCls} title="Refresh">↺ Refresh</button>
          <button onClick={fitView} className={btnCls} title="Fit to view">⊞ Fit</button>
          <button onClick={() => zoomBy(1.25)} className={btnCls} title="Zoom in">＋</button>
          <button onClick={() => zoomBy(0.8)} className={btnCls} title="Zoom out">－</button>
        </div>
      </div>

      {/* Filter / view bar */}
      <div className="flex flex-wrap items-center gap-2 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-3 flex-shrink-0">
        <select className={selCls} value={center} onChange={(e) => setCenter(e.target.value)}>
          <option value="">All Devices (center)</option>
          {devices.map((d) => <option key={d.id} value={String(d.id)} title={d.hostname}>{d.display_hostname || d.hostname}</option>)}
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
        <select className={selCls} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'all' | 'online' | 'offline')}>
          <option value="all">All Devices</option>
          <option value="online">Online Only</option>
          <option value="offline">Offline Only</option>
        </select>
        <select className={selCls} value={layoutMode} onChange={(e) => setLayoutMode(e.target.value as 'hier' | 'force')} title="Graph layout">
          <option value="hier">Hierarchical</option>
          <option value="force">Force-directed</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300 px-1">
          <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /> Labels
        </label>
        <label className="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300 px-1">
          <input type="checkbox" checked={showAPs} onChange={(e) => setShowAPs(e.target.checked)} /> APs
        </label>
        <button onClick={discoverAll} disabled={discovering} className="ml-auto px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
          {discovering ? 'Discovering…' : '🔍 Discover Links'}
        </button>
      </div>
      {toast && <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm text-green-800 flex-shrink-0">{toast}</div>}

      {/* Graph */}
      <div className="relative flex-1 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden" style={{ minHeight: 600 }}>
        {loading && <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-gray-800 z-10"><div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>}
        {error && <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-gray-800 z-10 text-center"><div><p className="text-5xl mb-3">🗺</p><p className="text-sm text-gray-500 dark:text-gray-400 max-w-xs">{error}</p></div></div>}
        {!loading && !error && nodeCount === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-gray-800 z-10 text-center">
            <div><p className="text-5xl mb-3">🌐</p><p className="text-lg font-semibold text-gray-700 dark:text-gray-300 mb-1">No devices match</p>
              <p className="text-sm text-gray-500 dark:text-gray-400">Adjust filters, or click Discover Links to map LLDP neighbors.</p></div>
          </div>
        )}
        <div ref={containerRef} className="w-full h-full" />

        {/* Legend (toggleable) */}
        <div className="absolute top-3 left-3 z-20">
          {legendOpen ? (
            <div className="bg-white/95 dark:bg-gray-900/95 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm text-[11px] w-48">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-200 dark:border-gray-700">
                <span className="font-semibold text-gray-700 dark:text-gray-200">Legend</span>
                <button onClick={() => setLegendOpen(false)} className="text-gray-400 hover:text-gray-600">×</button>
              </div>
              <div className="px-3 py-2 space-y-1 text-gray-600 dark:text-gray-300">
                {(['router', 'firewall', 'core-switch', 'distribution', 'access-switch', 'wireless-ap'] as const).map((k) => (
                  <div key={k} className="flex items-center gap-2">
                    <span style={{ color: NODE_STYLES[k].color }}>{NODE_STYLES[k].icon}</span>
                    <span className="capitalize">{k.replace('-', ' ')}</span>
                  </div>
                ))}
              </div>
              <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700 space-y-1 text-gray-600 dark:text-gray-300">
                <div className="flex items-center gap-2"><span className="w-6 h-[2px]" style={{ background: EDGE_UP }} />Single link</div>
                <div className="flex items-center gap-2"><span className="w-6 h-[3.5px]" style={{ background: EDGE_UP }} />×2 aggregated</div>
                <div className="flex items-center gap-2"><span className="w-6 h-[5px]" style={{ background: EDGE_UP }} />×3+ aggregated</div>
              </div>
              <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700 space-y-1 text-gray-600 dark:text-gray-300">
                <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full" style={{ background: EDGE_UP }} />Online</div>
                <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full border border-dashed" style={{ background: OFFLINE_COLOR, borderColor: OFFLINE_COLOR }} />Offline</div>
              </div>
            </div>
          ) : (
            <button onClick={() => setLegendOpen(true)} className={btnCls + ' bg-white/95 dark:bg-gray-900/95'}>Legend</button>
          )}
        </div>

        {/* Mini-map */}
        {mini && (
          <div className="absolute bottom-3 right-3 z-20 bg-white/95 dark:bg-gray-900/95 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-1">
            <svg width={mini.box.w} height={mini.box.h}>
              {mini.dots.map((d, i) => <circle key={i} cx={d.x} cy={d.y} r={2} fill={d.c} />)}
              <rect x={mini.view.x} y={mini.view.y} width={mini.view.w} height={mini.view.h}
                fill="none" stroke="#3b82f6" strokeWidth={1} />
            </svg>
          </div>
        )}

        {/* Hover tooltip */}
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
          <div className="absolute z-20 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700 p-4 w-64 text-sm"
            style={{ left: Math.min(popup.x + 12, (containerRef.current?.clientWidth ?? 600) - 270), top: popup.y + 12 }}>
            <button className="absolute top-2 right-2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 text-lg leading-none" onClick={() => setPopup(null)}>×</button>
            {popup.kind === 'node' ? (
              <>
                <p className="font-semibold text-gray-900 dark:text-gray-100 pr-4">{popup.data.label}</p>
                <p className="text-gray-500 dark:text-gray-400 text-xs mt-0.5 mb-3">{popup.data.type}{popup.data.role ? ` · ${popup.data.role}` : ''}</p>
                <div className="space-y-1.5">
                  <Row k="Status" v={
                    <span className="font-medium" style={{ color: isOffline(popup.data) ? OFFLINE_COLOR : '#22c55e' }}>
                      {isOffline(popup.data) ? '🔴 Offline' : '✅ Online'}
                    </span>
                  } />
                  {isOffline(popup.data) && <Row k="Last seen" v={relativeTime(popup.data.last_seen)} />}
                  {(popup.data.management_ip || popup.data.ip) && <Row k="IP" v={<span className="font-mono text-xs">{popup.data.management_ip || popup.data.ip}</span>} />}
                  {popup.data.model && <Row k="Model" v={popup.data.model} />}
                  {popup.data.site && <Row k="Site" v={popup.data.site} />}
                  {isAP(popup.data) && popup.data.client_count != null && <Row k="Clients" v={`📶 ${popup.data.client_count}`} />}
                  {isAP(popup.data) && apRadioSummary(popup.data) && <Row k="Radios" v={apRadioSummary(popup.data)} />}
                  {!isAP(popup.data) && <Row k="Neighbors" v={popup.data.neighbor_count ?? 0} />}
                </div>
                <button onClick={() => navigate(`/devices/${popup.data.id}`)} className="mt-3 w-full py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-md font-medium">View Device →</button>
              </>
            ) : (
              <>
                <p className="font-semibold text-gray-900 dark:text-gray-100 mb-1">Link{(popup.data.link_count ?? 1) > 1 ? ` (×${popup.data.link_count})` : ''}</p>
                <div className="text-xs font-mono text-gray-700 dark:text-gray-300 mb-3 space-y-0.5">
                  {(popup.data.links && popup.data.links.length ? popup.data.links : [{ port_a: popup.data.port_a, port_b: popup.data.port_b, speed_mbps: popup.data.speed_mbps }]).map((m, i) => (
                    <div key={i}>{m.port_a || '?'} ↔ {m.port_b || '?'}</div>
                  ))}
                </div>
                <div className="space-y-1.5">
                  <Row k="Speed" v={fmtSpeed(popup.data.speed_mbps)} />
                  <Row k="Links" v={popup.data.link_count ?? 1} />
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between"><span className="text-gray-500 dark:text-gray-400">{k}</span><span className="text-gray-800 dark:text-gray-100">{v}</span></div>
}

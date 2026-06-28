import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { useTabParam } from '../lib/useTabParam'
import {
  fetchSite, fetchSiteDevices, saveSite, fetchSites, fetchDevices, setDeviceSite,
  fetchCollectors, fetchSiteServers, fetchChecks,
  type Site, type Device, type Collector, type Server, type ServiceCheck,
} from '../api/client'
import SiteFormModal from '../components/SiteFormModal'
import DeviceLink from '../components/DeviceLink'
import SiteCredentialsSection from '../components/SiteCredentialsSection'
import CircuitCard from '../components/CircuitCard'
import CircuitModal from '../components/CircuitModal'
import { fetchCircuits, deleteCircuit, type WanCircuit } from '../api/client'

const TYPE_ICON: Record<string, string> = {
  datacenter: '🏢', campus: '🏫', branch: '🏬', remote: '📡', cloud: '☁️',
}
const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  inactive: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
  maintenance: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  decommissioned: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
}
const TABS = ['Overview', 'Devices', 'Servers', 'Service Checks', 'Availability', 'WAN Circuits'] as const

export default function SiteDetail() {
  const { id } = useParams<{ id: string }>()
  const siteId = Number(id)
  const navigate = useNavigate()
  const [site, setSite] = useState<Site | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Active tab in the URL (?tab=…) so a refresh restores it and links are shareable.
  const [tab, setTab] = useTabParam(TABS, 'Overview')
  const [editing, setEditing] = useState(false)
  const [allSites, setAllSites] = useState<Site[]>([])

  const load = useCallback(() => {
    setLoading(true)
    fetchSite(siteId)
      .then((s) => { setSite(s); setError(null) })
      .catch(() => setError('Site not found or the API is unavailable.'))
      .finally(() => setLoading(false))
  }, [siteId])
  useEffect(() => { load() }, [load])
  // All sites — only needed to populate the edit modal's parent-site dropdown.
  useEffect(() => { fetchSites().then(setAllSites).catch(() => {}) }, [])

  if (loading) return <div className="flex items-center justify-center py-24"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (error || !site) {
    return (
      <div className="space-y-4">
        <Link to="/sites" className="text-sm text-blue-600 hover:text-blue-800">&larr; Back to sites</Link>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error ?? 'Site not found.'}</div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <Link to="/sites" className="text-sm text-blue-600 hover:text-blue-800">&larr; Sites</Link>
        <div className="flex items-center gap-3 mt-2">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{TYPE_ICON[site.site_type]} {site.name}</h1>
          <span className="px-2 py-0.5 rounded-full text-xs font-medium capitalize bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">{site.site_type}</span>
          {site.parent_site_name && <span className="text-sm text-gray-400 dark:text-gray-500">in {site.parent_site_name}</span>}
          <button
            onClick={() => setEditing(true)}
            className="ml-auto px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50"
          >
            Edit
          </button>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          {[site.city, site.state, site.country].filter(Boolean).join(', ') || 'No location set'}
        </p>
      </div>

      {editing && (
        <SiteFormModal
          site={site}
          sites={allSites}
          onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); load() }}
          onDeleted={() => navigate('/sites', { state: { toast: `Site "${site.name}" deleted` } })}
        />
      )}

      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={clsx('px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap', tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200')}>{t}</button>
        ))}
      </div>

      {tab === 'Overview' && <Overview site={site} />}
      {tab === 'Devices' && <Devices siteId={site.id} onOpen={(d) => navigate(`/devices/${d}`)} onChanged={load} />}
      {tab === 'Servers' && <Servers siteId={site.id} onOpen={(id) => navigate(`/servers/${id}`)} />}
      {tab === 'Service Checks' && <ServiceChecks siteId={site.id} onOpen={() => navigate('/checks')} />}
      {tab === 'Availability' && <Placeholder text="Site-level uptime summary appears once availability records are computed." icon="📈" />}
      {tab === 'WAN Circuits' && <SiteCircuits siteId={site.id} />}
    </div>
  )
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={clsx('bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4', className)}>{children}</div>
}

function StatCard({ label, value, tone }: { label: string; value: React.ReactNode; tone?: 'up' | 'down' | 'unknown' }) {
  const toneCls =
    tone === 'up' ? 'text-green-600 dark:text-green-400'
    : tone === 'down' ? 'text-red-600 dark:text-red-400'
    : tone === 'unknown' ? 'text-gray-400 dark:text-gray-500'
    : 'text-gray-900 dark:text-gray-100'
  return (
    <Card className="text-center">
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</div>
      <div className={clsx('mt-1 text-2xl font-bold tabular-nums', toneCls)}>{value}</div>
    </Card>
  )
}

function StatSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">{title}</div>
      {children}
    </div>
  )
}

function DeviceStats({ site }: { site: Site }) {
  return (
    <StatSection title="Devices">
      <div className={clsx('grid gap-4', site.devices_unknown > 0 ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-1 sm:grid-cols-3')}>
        <StatCard label="Total Devices" value={site.device_count} />
        <StatCard label="Online" value={<span>↑ {site.devices_up}</span>} tone="up" />
        <StatCard label="Offline" value={<span>↓ {site.devices_down}</span>} tone="down" />
        {site.devices_unknown > 0 && <StatCard label="Unknown" value={<span>? {site.devices_unknown}</span>} tone="unknown" />}
      </div>
    </StatSection>
  )
}

function ServerStats({ site }: { site: Site }) {
  return (
    <StatSection title="Servers">
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-3">
        <StatCard label="Total Servers" value={site.server_count} />
        <StatCard label="Online" value={<span>↑ {site.servers_up}</span>} tone="up" />
        <StatCard label="Offline" value={<span>↓ {site.servers_down}</span>} tone="down" />
      </div>
    </StatSection>
  )
}

function CheckStats({ site }: { site: Site }) {
  return (
    <StatSection title="Service Checks">
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-3">
        <StatCard label="Total Checks" value={site.check_count} />
        <StatCard label="Passing" value={<span>↑ {site.checks_up}</span>} tone="up" />
        <StatCard label="Failing" value={<span>↓ {site.checks_down}</span>} tone="down" />
      </div>
    </StatSection>
  )
}

function Overview({ site }: { site: Site }) {
  const hasGeo = site.latitude && site.longitude
  return (
    <div className="space-y-4">
    <DeviceStats site={site} />
    <ServerStats site={site} />
    <CheckStats site={site} />
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card className="lg:col-span-2">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Location</h3>
        {hasGeo ? (
          <a
            href={`https://www.openstreetmap.org/?mlat=${site.latitude}&mlon=${site.longitude}#map=12/${site.latitude}/${site.longitude}`}
            target="_blank" rel="noreferrer"
            className="block bg-gradient-to-br from-blue-50 to-gray-100 dark:from-blue-900/20 dark:to-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg h-40 flex items-center justify-center text-center hover:from-blue-100 dark:hover:from-blue-900/30"
          >
            <span className="text-sm text-gray-600 dark:text-gray-400">📍 {site.latitude}, {site.longitude}<br /><span className="text-xs text-blue-600">Open in map →</span></span>
          </a>
        ) : (
          <div className="bg-gray-50 dark:bg-gray-900/50 border border-dashed border-gray-200 dark:border-gray-600 rounded-lg h-40 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">No coordinates set</div>
        )}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm mt-4">
          <Info label="Address" value={site.address || '—'} />
          <Info label="City" value={site.city || '—'} />
          <Info label="State" value={site.state || '—'} />
          <Info label="Country" value={site.country || '—'} />
        </dl>
      </Card>
      <div className="space-y-4">
        <Card>
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Contact</h3>
          <dl className="space-y-2 text-sm">
            <Info label="Name" value={site.contact_name || '—'} />
            <Info label="Email" value={site.contact_email || '—'} />
            <Info label="Phone" value={site.contact_phone || '—'} />
          </dl>
        </Card>
        <DefaultCollectorCard site={site} />
        <Card>
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Notes</h3>
          <p className="text-sm text-gray-600 dark:text-gray-400 whitespace-pre-wrap">{site.notes || '—'}</p>
        </Card>
      </div>
    </div>
    <SiteCredentialsSection siteId={site.id} />
    </div>
  )
}

function Devices({ siteId, onOpen, onChanged }: { siteId: number; onOpen: (id: number) => void; onChanged?: () => void }) {
  const [devices, setDevices] = useState<Device[]>([])
  const [allDevices, setAllDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [addId, setAddId] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    return Promise.all([
      fetchSiteDevices(siteId),
      fetchDevices({ page_size: '1000' }).then((r) => r.results).catch(() => [] as Device[]),
    ]).then(([here, all]) => { setDevices(here); setAllDevices(all) })
      .catch(() => setDevices([]))
      .finally(() => setLoading(false))
  }, [siteId])
  useEffect(() => { refresh() }, [refresh])

  const assignedIds = new Set(devices.map((d) => d.id))
  const available = allDevices.filter((d) => !assignedIds.has(d.id))

  const assign = async (deviceId: number) => {
    setBusy(true)
    try { await setDeviceSite(deviceId, siteId); setAddId(''); await refresh(); onChanged?.() }
    finally { setBusy(false) }
  }
  const unassign = async (deviceId: number) => {
    setBusy(true)
    try { await setDeviceSite(deviceId, null); await refresh(); onChanged?.() }
    finally { setBusy(false) }
  }

  if (loading) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  return (
    <div className="space-y-4">
      {/* Assign a device to this site */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 flex flex-col sm:flex-row sm:items-end gap-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Add a device to this site</label>
          <select
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            value={addId}
            disabled={busy || available.length === 0}
            onChange={(e) => setAddId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">{available.length === 0 ? 'No unassigned devices available' : '— Select a device —'}</option>
            {available.map((d) => (
              <option key={d.id} value={d.id}>{d.hostname} ({d.ip_address}){d.site_name ? ` — currently ${d.site_name}` : ''}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => addId && assign(addId)}
          disabled={busy || !addId}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
        >
          Add to site
        </button>
      </div>

      {devices.length === 0 ? (
        <Placeholder text="No devices are located at this site yet. Use the selector above to assign one." icon="📡" />
      ) : (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Hostname</th>
                <th className="px-5 py-3 font-medium">IP</th>
                <th className="px-5 py-3 font-medium">Platform</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {devices.map((d) => (
                <tr key={d.id} onClick={() => onOpen(d.id)} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
                  <td className="px-5 py-3 font-medium" onClick={(e) => e.stopPropagation()}>
                    <DeviceLink deviceId={d.id} hostname={d.hostname} />
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400 font-mono text-xs">{d.ip_address}</td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{d.platform}</td>
                  <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[d.status] ?? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400')}>{d.status}</span></td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={(e) => { e.stopPropagation(); unassign(d.id) }}
                      disabled={busy}
                      className="text-red-600 hover:text-red-800 dark:text-red-400 text-sm font-medium disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// "Online" mirrors the site server-count logic: ACTIVE + a heartbeat within 5
// minutes (keep in lockstep with servers_up/down so the tab matches the summary).
const SERVER_OFFLINE_MS = 5 * 60 * 1000
function serverOnline(s: Server): boolean {
  return s.status === 'active' && !!s.last_seen &&
    Date.now() - new Date(s.last_seen).getTime() < SERVER_OFFLINE_MS
}
function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function Servers({ siteId, onOpen }: { siteId: number; onOpen: (id: string) => void }) {
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchSiteServers(siteId)
      .then(setServers)
      .catch(() => setServers([]))
      .finally(() => setLoading(false))
  }, [siteId])

  if (loading) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (servers.length === 0) {
    return <Placeholder text="No servers at this site. Servers appear here when an agent is enrolled to this site or reassigned from a server's detail page." icon="🖥️" />
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-5 py-3 font-medium">Hostname</th>
            <th className="px-5 py-3 font-medium">OS</th>
            <th className="px-5 py-3 font-medium">Status</th>
            <th className="px-5 py-3 font-medium">Last seen</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {servers.map((s) => {
            const online = serverOnline(s)
            return (
              <tr key={s.id} onClick={() => onOpen(s.id)} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
                <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">{s.hostname}</td>
                <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{s.os_version || s.os || '—'}</td>
                <td className="px-5 py-3">
                  <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium',
                    online ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400')}>
                    {online ? '↑ Online' : '↓ Offline'}
                  </span>
                </td>
                <td className="px-5 py-3 text-gray-500 dark:text-gray-400">{timeAgo(s.last_seen)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const CHECK_STATUS_COLORS: Record<string, string> = {
  up: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  down: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  degraded: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}

function ServiceChecks({ siteId, onOpen }: { siteId: number; onOpen: () => void }) {
  const [checks, setChecks] = useState<ServiceCheck[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchChecks({ site: String(siteId) })
      .then(setChecks)
      .catch(() => setChecks([]))
      .finally(() => setLoading(false))
  }, [siteId])

  if (loading) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (checks.length === 0) {
    return <Placeholder text="No service checks target this site." icon="✓" />
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-5 py-3 font-medium">Name</th>
            <th className="px-5 py-3 font-medium">Type</th>
            <th className="px-5 py-3 font-medium">Target</th>
            <th className="px-5 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {checks.map((c) => (
            <tr key={c.id} onClick={onOpen} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
              <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">
                {c.name}
                {!c.is_active && <span className="ml-2 text-xs text-gray-400">(paused)</span>}
              </td>
              <td className="px-5 py-3 text-gray-600 dark:text-gray-400 uppercase">{c.check_type}</td>
              <td className="px-5 py-3 text-gray-600 dark:text-gray-400 font-mono text-xs">{c.host}{c.effective_port ? `:${c.effective_port}` : ''}</td>
              <td className="px-5 py-3">
                <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                  CHECK_STATUS_COLORS[c.current_status] ?? CHECK_STATUS_COLORS.unknown)}>
                  {c.current_status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Placeholder({ text, icon }: { text: string; icon: string }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 py-16 text-center">
      <div className="text-4xl mb-2">{icon}</div>
      <p className="text-sm text-gray-500 dark:text-gray-400">{text}</p>
    </div>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt><dd className="text-gray-800 dark:text-gray-100">{value}</dd></div>
}

function SiteCircuits({ siteId }: { siteId: number }) {
  const [circuits, setCircuits] = useState<WanCircuit[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<WanCircuit | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchCircuits({ site: String(siteId) }).then(setCircuits).catch(() => {}).finally(() => setLoading(false))
  }, [siteId])
  useEffect(load, [load])

  const remove = async (c: WanCircuit) => {
    if (!window.confirm(`Delete circuit "${c.name}"?`)) return
    try { await deleteCircuit(c.id); load() } catch { /* ignore */ }
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button onClick={() => setAdding(true)} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Add Circuit</button>
      </div>
      {loading ? (
        <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
      ) : circuits.length === 0 ? (
        <Placeholder text="No WAN circuits at this site yet. Add one to track provider, bandwidth, IPs and utilization." icon="🔌" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {circuits.map((c) => <CircuitCard key={c.id} circuit={c} onEdit={setEditing} onDelete={remove} />)}
        </div>
      )}
      {adding && <CircuitModal prefillSite={siteId} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load() }} />}
      {editing && <CircuitModal edit={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}
    </div>
  )
}

function DefaultCollectorCard({ site }: { site: Site }) {
  const [collectors, setCollectors] = useState<Collector[]>([])
  const [sel, setSel] = useState<number | ''>(site.default_collector ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => { fetchCollectors().then(setCollectors).catch(() => {}) }, [])

  const save = async (value: number | '') => {
    setSel(value); setSaving(true); setSaved(false)
    try {
      await saveSite({ name: site.name, default_collector: value === '' ? null : Number(value) }, site.id)
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } finally { setSaving(false) }
  }

  return (
    <Card>
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Default Collector</h3>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-2">Devices added at this site use this collector when none is chosen.</p>
      <select className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        value={sel} disabled={saving} onChange={(e) => save(e.target.value === '' ? '' : Number(e.target.value))}>
        <option value="">— None (use global default) —</option>
        {collectors.map((c) => (
          <option key={c.id} value={c.id}>{c.name}{c.collector_ip ? ` (${c.collector_ip})` : ''}{c.is_default ? ' — default' : ''}</option>
        ))}
      </select>
      {saving && <p className="text-xs text-gray-400 mt-1">Saving…</p>}
      {saved && <p className="text-xs text-green-600 dark:text-green-400 mt-1">✓ Saved</p>}
    </Card>
  )
}

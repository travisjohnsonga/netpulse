import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { fetchSite, fetchSiteDevices, type Site, type Device } from '../api/client'

const TYPE_ICON: Record<string, string> = {
  datacenter: '🏢', campus: '🏫', branch: '🏬', remote: '📡', cloud: '☁️',
}
const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-gray-100 text-gray-600',
  maintenance: 'bg-yellow-100 text-yellow-700',
  decommissioned: 'bg-red-100 text-red-700',
}
const TABS = ['Overview', 'Devices', 'Availability', 'WAN Circuits'] as const

export default function SiteDetail() {
  const { id } = useParams<{ id: string }>()
  const siteId = Number(id)
  const navigate = useNavigate()
  const [site, setSite] = useState<Site | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<string>('Overview')

  const load = useCallback(() => {
    setLoading(true)
    fetchSite(siteId)
      .then((s) => { setSite(s); setError(null) })
      .catch(() => setError('Site not found or the API is unavailable.'))
      .finally(() => setLoading(false))
  }, [siteId])
  useEffect(() => { load() }, [load])

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
          <h1 className="text-2xl font-bold text-gray-900">{TYPE_ICON[site.site_type]} {site.name}</h1>
          <span className="px-2 py-0.5 rounded-full text-xs font-medium capitalize bg-blue-100 text-blue-700">{site.site_type}</span>
          {site.parent_site_name && <span className="text-sm text-gray-400">in {site.parent_site_name}</span>}
        </div>
        <p className="text-sm text-gray-500 mt-0.5">
          {[site.city, site.state, site.country].filter(Boolean).join(', ') || 'No location set'}
        </p>
      </div>

      <div className="flex gap-1 border-b border-gray-200 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={clsx('px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap', tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800')}>{t}</button>
        ))}
      </div>

      {tab === 'Overview' && <Overview site={site} />}
      {tab === 'Devices' && <Devices siteId={site.id} onOpen={(d) => navigate(`/devices/${d}`)} />}
      {tab === 'Availability' && <Placeholder text="Site-level uptime summary appears once availability records are computed." icon="📈" />}
      {tab === 'WAN Circuits' && <Placeholder text="WAN circuits connecting this site will appear here (circuit overrides backend pending)." icon="🔌" />}
    </div>
  )
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={clsx('bg-white rounded-lg shadow-sm border border-gray-200 p-4', className)}>{children}</div>
}

function Overview({ site }: { site: Site }) {
  const hasGeo = site.latitude && site.longitude
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card className="lg:col-span-2">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Location</h3>
        {hasGeo ? (
          <a
            href={`https://www.openstreetmap.org/?mlat=${site.latitude}&mlon=${site.longitude}#map=12/${site.latitude}/${site.longitude}`}
            target="_blank" rel="noreferrer"
            className="block bg-gradient-to-br from-blue-50 to-gray-100 border border-gray-200 rounded-lg h-40 flex items-center justify-center text-center hover:from-blue-100"
          >
            <span className="text-sm text-gray-600">📍 {site.latitude}, {site.longitude}<br /><span className="text-xs text-blue-600">Open in map →</span></span>
          </a>
        ) : (
          <div className="bg-gray-50 border border-dashed border-gray-200 rounded-lg h-40 flex items-center justify-center text-sm text-gray-400">No coordinates set</div>
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
          <h3 className="text-sm font-semibold text-gray-800 mb-3">Contact</h3>
          <dl className="space-y-2 text-sm">
            <Info label="Name" value={site.contact_name || '—'} />
            <Info label="Email" value={site.contact_email || '—'} />
            <Info label="Phone" value={site.contact_phone || '—'} />
          </dl>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-2">Notes</h3>
          <p className="text-sm text-gray-600 whitespace-pre-wrap">{site.notes || '—'}</p>
        </Card>
      </div>
    </div>
  )
}

function Devices({ siteId, onOpen }: { siteId: number; onOpen: (id: number) => void }) {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => { fetchSiteDevices(siteId).then(setDevices).catch(() => setDevices([])).finally(() => setLoading(false)) }, [siteId])

  if (loading) return <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (devices.length === 0) return <Placeholder text="No devices are located at this site yet." icon="📡" />

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
            <th className="px-5 py-3 font-medium">Hostname</th>
            <th className="px-5 py-3 font-medium">IP</th>
            <th className="px-5 py-3 font-medium">Platform</th>
            <th className="px-5 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {devices.map((d) => (
            <tr key={d.id} onClick={() => onOpen(d.id)} className="hover:bg-gray-50 cursor-pointer">
              <td className="px-5 py-3 font-medium text-gray-800">{d.hostname}</td>
              <td className="px-5 py-3 text-gray-600 font-mono text-xs">{d.ip_address}</td>
              <td className="px-5 py-3 text-gray-600">{d.platform}</td>
              <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[d.status] ?? 'bg-gray-100 text-gray-600')}>{d.status}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Placeholder({ text, icon }: { text: string; icon: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 py-16 text-center">
      <div className="text-4xl mb-2">{icon}</div>
      <p className="text-sm text-gray-500">{text}</p>
    </div>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-gray-400">{label}</dt><dd className="text-gray-800">{value}</dd></div>
}

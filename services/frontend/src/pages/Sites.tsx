import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { fetchSites, saveSite, deleteSite, type Site, type SitePayload, type SiteType } from '../api/client'
import EmptyState from '../components/EmptyState'
import Modal from '../components/Modal'

const TYPE_BADGE: Record<SiteType, string> = {
  datacenter: 'bg-purple-100 text-purple-700',
  campus: 'bg-blue-100 text-blue-700',
  branch: 'bg-green-100 text-green-700',
  remote: 'bg-yellow-100 text-yellow-700',
  cloud: 'bg-sky-100 text-sky-700',
}
const TYPE_ICON: Record<SiteType, string> = {
  datacenter: '🏢', campus: '🏫', branch: '🏬', remote: '📡', cloud: '☁️',
}
const SITE_TYPES: SiteType[] = ['datacenter', 'campus', 'branch', 'remote', 'cloud']

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

export default function Sites() {
  const navigate = useNavigate()
  const [sites, setSites] = useState<Site[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'table' | 'tree'>('table')
  const [editing, setEditing] = useState<Site | 'new' | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchSites()
      .then((s) => { setSites(s); setError(null) })
      .catch(() => setError('Failed to load sites. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Sites</h1>
          <p className="text-sm text-gray-500 mt-0.5">{sites.length} site{sites.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex gap-2">
          <div className="flex rounded-lg border border-gray-300 overflow-hidden">
            <button onClick={() => setView('table')} className={clsx('px-3 py-2 text-sm', view === 'table' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')}>Table</button>
            <button onClick={() => setView('tree')} className={clsx('px-3 py-2 text-sm', view === 'tree' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')}>Hierarchy</button>
          </div>
          <button onClick={() => setEditing('new')} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Add Site</button>
        </div>
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : sites.length === 0 ? (
          <EmptyState title="No sites yet" description="Add datacenters, campuses and branches to organize your devices." action={{ label: 'Add Site', onClick: () => setEditing('new') }} icon="🏢" />
        ) : view === 'table' ? (
          <TableView sites={sites} onOpen={(id) => navigate(`/sites/${id}`)} />
        ) : (
          <TreeView sites={sites} onOpen={(id) => navigate(`/sites/${id}`)} />
        )}
      </div>

      {editing && (
        <SiteFormModal
          site={editing === 'new' ? null : editing}
          sites={sites}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
          onDelete={editing !== 'new' ? () => { deleteSite(editing.id).then(() => { setEditing(null); load() }) } : undefined}
        />
      )}
    </div>
  )
}

function TypeBadge({ t }: { t: SiteType }) {
  return <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', TYPE_BADGE[t])}>{TYPE_ICON[t]} {t}</span>
}

function TableView({ sites, onOpen }: { sites: Site[]; onOpen: (id: number) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
            <th className="px-5 py-3 font-medium">Name</th>
            <th className="px-5 py-3 font-medium">Type</th>
            <th className="px-5 py-3 font-medium">City</th>
            <th className="px-5 py-3 font-medium">Devices</th>
            <th className="px-5 py-3 font-medium">Parent</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sites.map((s) => (
            <tr key={s.id} onClick={() => onOpen(s.id)} className="hover:bg-gray-50 cursor-pointer">
              <td className="px-5 py-3 font-medium text-gray-800">{s.name}</td>
              <td className="px-5 py-3"><TypeBadge t={s.site_type} /></td>
              <td className="px-5 py-3 text-gray-600">{s.city || '—'}</td>
              <td className="px-5 py-3 text-gray-600">{s.device_count}</td>
              <td className="px-5 py-3 text-gray-500">{s.parent_site_name || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TreeView({ sites, onOpen }: { sites: Site[]; onOpen: (id: number) => void }) {
  const byParent = useMemo(() => {
    const m = new Map<number | null, Site[]>()
    for (const s of sites) {
      const k = s.parent_site
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(s)
    }
    return m
  }, [sites])

  const render = (parent: number | null, depth: number): React.ReactNode =>
    (byParent.get(parent) ?? []).map((s) => (
      <div key={s.id}>
        <div
          onClick={() => onOpen(s.id)}
          className="flex items-center gap-2 px-5 py-2.5 hover:bg-gray-50 cursor-pointer border-b border-gray-50"
          style={{ paddingLeft: `${1.25 + depth * 1.5}rem` }}
        >
          {depth > 0 && <span className="text-gray-300">└</span>}
          <span className="font-medium text-gray-800">{s.name}</span>
          <TypeBadge t={s.site_type} />
          <span className="ml-auto text-xs text-gray-400">{s.device_count} devices</span>
        </div>
        {render(s.id, depth + 1)}
      </div>
    ))

  return <div>{render(null, 0)}</div>
}

function SiteFormModal({ site, sites, onClose, onSaved, onDelete }: {
  site: Site | null
  sites: Site[]
  onClose: () => void
  onSaved: () => void
  onDelete?: () => void
}) {
  const isEdit = !!site
  const [f, setF] = useState<SitePayload>(() => ({
    name: site?.name ?? '',
    site_type: site?.site_type ?? 'branch',
    description: site?.description ?? '',
    address: site?.address ?? '',
    city: site?.city ?? '',
    state: site?.state ?? '',
    country: site?.country ?? '',
    latitude: site?.latitude ?? null,
    longitude: site?.longitude ?? null,
    parent_site: site?.parent_site ?? null,
    contact_name: site?.contact_name ?? '',
    contact_email: site?.contact_email ?? '',
    contact_phone: site?.contact_phone ?? '',
    notes: site?.notes ?? '',
  }))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const set = (k: keyof SitePayload, v: unknown) => setF((p) => ({ ...p, [k]: v }))

  const submit = async () => {
    if (!f.name?.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    try { await saveSite(f, site?.id); onSaved() }
    catch (e) {
      const d = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof d === 'object' ? JSON.stringify(d) : 'Failed to save site.'); setSaving(false)
    }
  }

  const parentOptions = sites.filter((s) => s.id !== site?.id)

  return (
    <Modal
      title={isEdit ? `Edit: ${site!.name}` : 'New Site'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          {onDelete && <button onClick={onDelete} className="py-2.5 px-4 border border-red-200 text-red-600 rounded-lg text-sm font-medium hover:bg-red-50">Delete</button>}
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : isEdit ? 'Save' : 'Create'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
        <div className="flex gap-3">
          <Field label="Name"><input className={inputCls} value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="Type">
            <select className={inputCls} value={f.site_type} onChange={(e) => set('site_type', e.target.value)}>
              {SITE_TYPES.map((t) => <option key={t} value={t}>{TYPE_ICON[t]} {t}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Parent site">
          <select className={inputCls} value={f.parent_site ?? ''} onChange={(e) => set('parent_site', e.target.value ? Number(e.target.value) : null)}>
            <option value="">— None —</option>
            {parentOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Address"><input className={inputCls} value={f.address} onChange={(e) => set('address', e.target.value)} /></Field>
        <div className="flex gap-3">
          <Field label="City"><input className={inputCls} value={f.city} onChange={(e) => set('city', e.target.value)} /></Field>
          <Field label="State"><input className={inputCls} value={f.state} onChange={(e) => set('state', e.target.value)} /></Field>
          <Field label="Country"><input className={inputCls} value={f.country} onChange={(e) => set('country', e.target.value)} /></Field>
        </div>
        <div className="flex gap-3">
          <Field label="Latitude"><input className={inputCls} value={f.latitude ?? ''} onChange={(e) => set('latitude', e.target.value || null)} placeholder="optional" /></Field>
          <Field label="Longitude"><input className={inputCls} value={f.longitude ?? ''} onChange={(e) => set('longitude', e.target.value || null)} placeholder="optional" /></Field>
        </div>
        <div className="flex gap-3">
          <Field label="Contact name"><input className={inputCls} value={f.contact_name} onChange={(e) => set('contact_name', e.target.value)} /></Field>
          <Field label="Contact email"><input className={inputCls} value={f.contact_email} onChange={(e) => set('contact_email', e.target.value)} /></Field>
          <Field label="Contact phone"><input className={inputCls} value={f.contact_phone} onChange={(e) => set('contact_phone', e.target.value)} /></Field>
        </div>
        <Field label="Notes"><textarea className={`${inputCls} h-20`} value={f.notes} onChange={(e) => set('notes', e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex-1 min-w-0"><label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>{children}</div>
}

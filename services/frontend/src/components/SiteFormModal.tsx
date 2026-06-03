import { useState } from 'react'
import Modal from './Modal'
import { saveSite, deleteSite, type Site, type SitePayload, type SiteType } from '../api/client'

const SITE_TYPES: SiteType[] = ['datacenter', 'campus', 'branch', 'remote', 'cloud']
const TYPE_ICON: Record<SiteType, string> = {
  datacenter: '🏢', campus: '🏫', branch: '🏬', remote: '📡', cloud: '☁️',
}

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex-1 min-w-0"><label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{label}</label>{children}</div>
}

/**
 * Create / edit a Site, with an in-modal delete confirmation. Shared by the
 * Sites list and the Site detail page so both reach the same form.
 */
export default function SiteFormModal({ site, sites, onClose, onSaved, onDeleted }: {
  site: Site | null
  sites: Site[]
  onClose: () => void
  onSaved: (saved: Site) => void
  /** Called after the site is deleted (omit to hide the Delete button). */
  onDeleted?: () => void
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
  const [confirmDelete, setConfirmDelete] = useState(false)
  const set = (k: keyof SitePayload, v: unknown) => setF((p) => ({ ...p, [k]: v }))

  const submit = async () => {
    if (!f.name?.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    try { const saved = await saveSite(f, site?.id); onSaved(saved) }
    catch (e) {
      const d = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof d === 'object' ? JSON.stringify(d) : 'Failed to save site.'); setSaving(false)
    }
  }

  const doDelete = async () => {
    if (!site) return
    setSaving(true); setErr(null)
    try { await deleteSite(site.id); onDeleted?.() }
    catch { setErr('Failed to delete site.'); setSaving(false) }
  }

  const parentOptions = sites.filter((s) => s.id !== site?.id)

  // Delete confirmation view.
  if (confirmDelete && site) {
    return (
      <Modal
        title={`Delete ${site.name}?`}
        onClose={() => setConfirmDelete(false)}
        footer={
          <>
            <button onClick={() => setConfirmDelete(false)} disabled={saving} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
            <button onClick={doDelete} disabled={saving} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Deleting…' : 'Delete site'}</button>
          </>
        }
      >
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 mb-3">{err}</div>}
        <p className="text-sm text-gray-600 dark:text-gray-300">
          Delete site <span className="font-semibold">{site.name}</span>? Devices assigned to this
          site will be unassigned (not deleted). This cannot be undone.
        </p>
      </Modal>
    )
  }

  return (
    <Modal
      title={isEdit ? `Edit: ${site!.name}` : 'New Site'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          {onDeleted && isEdit && <button onClick={() => setConfirmDelete(true)} className="py-2.5 px-4 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg text-sm font-medium hover:bg-red-50 dark:hover:bg-red-900/20">Delete</button>}
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
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
        <Field label="Description"><input className={inputCls} value={f.description} onChange={(e) => set('description', e.target.value)} /></Field>
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

import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import OSStatusBadge from '../../components/OSStatusBadge'
import { SectionHeader } from '../Settings'
import { parseApiErrors } from '../../api/errors'
import {
  fetchApprovedOSVersions, createApprovedOSVersion, updateApprovedOSVersion,
  deleteApprovedOSVersion, fetchDevicePlatforms, fetchDiscoveredPlatforms,
  type ApprovedOSVersion, type ApprovedOSVersionPayload, type OSPolicyStatus,
  type PlatformOption,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const STATUSES: { value: OSPolicyStatus; label: string }[] = [
  { value: 'preferred',  label: '🟢 Preferred' },
  { value: 'approved',   label: '🟡 Approved' },
  { value: 'deprecated', label: '🟠 Deprecated' },
  { value: 'prohibited', label: '🔴 Prohibited' },
]

const EMPTY: ApprovedOSVersionPayload = {
  platform: '', version_pattern: '', is_regex: false, status: 'approved', notes: '',
}

export default function OSVersions() {
  const [rows, setRows] = useState<ApprovedOSVersion[]>([])
  const [platforms, setPlatforms] = useState<PlatformOption[]>([])
  // Platforms actually present in the fleet (handy defaults for the dropdown).
  const [fleetPlatforms, setFleetPlatforms] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<ApprovedOSVersion | null>(null)
  const [form, setForm] = useState<ApprovedOSVersionPayload | null>(null)
  const [formErr, setFormErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<ApprovedOSVersion | null>(null)

  const load = () => {
    setLoading(true)
    Promise.all([fetchApprovedOSVersions(), fetchDevicePlatforms(), fetchDiscoveredPlatforms()])
      .then(([r, p, d]) => {
        setRows(r); setPlatforms(p)
        setFleetPlatforms([...new Set(d.map((x) => x.platform))].sort())
        setError(null)
      })
      .catch(() => setError('Failed to load OS version policies.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const openCreate = () => { setEditing(null); setForm({ ...EMPTY }); setFormErr(null) }
  const openEdit = (r: ApprovedOSVersion) => {
    setEditing(r)
    setForm({ platform: r.platform, version_pattern: r.version_pattern, is_regex: r.is_regex, status: r.status, notes: r.notes })
    setFormErr(null)
  }

  const save = async () => {
    if (!form) return
    if (!form.platform.trim() || !form.version_pattern.trim()) {
      setFormErr('Platform and version pattern are required.')
      return
    }
    setSaving(true)
    try {
      if (editing) await updateApprovedOSVersion(editing.id, form)
      else await createApprovedOSVersion(form)
      setForm(null); setEditing(null); load()
    } catch (e) {
      setFormErr(parseApiErrors(e) || 'Failed to save policy.')
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    try { await deleteApprovedOSVersion(deleting.id); setDeleting(null); load() }
    catch { setError('Failed to delete policy.'); setDeleting(null) }
  }

  // Union of backend platform list + fleet platforms for the dropdown.
  const platformOptions = [...new Set([
    ...fleetPlatforms,
    ...platforms.map((p) => p.value),
  ])].sort()

  return (
    <div>
      <SectionHeader
        title="Approved OS Versions"
        description="Define which OS versions are preferred, approved, deprecated, or prohibited per platform. Devices are scored against these policies."
        action={
          <button onClick={openCreate} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            + Add Policy
          </button>
        }
      />

      {error && <div className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-sm text-gray-400">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">
            No OS version policies yet. Add one to start tracking OS compliance.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Platform</th>
                <th className="px-5 py-3 font-medium">Version</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Notes</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rows.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-5 py-3 font-mono text-xs text-gray-700 dark:text-gray-200">{r.platform}</td>
                  <td className="px-5 py-3 font-mono text-xs">
                    {r.version_pattern}
                    {r.is_regex && <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300">regex</span>}
                  </td>
                  <td className="px-5 py-3"><OSStatusBadge status={r.status} /></td>
                  <td className="px-5 py-3 text-gray-500 dark:text-gray-400 max-w-xs truncate">{r.notes || '—'}</td>
                  <td className="px-5 py-3">
                    <div className="flex gap-2 justify-end">
                      <button onClick={() => openEdit(r)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50">Edit</button>
                      <button onClick={() => setDeleting(r)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {form && (
        <Modal
          title={editing ? 'Edit OS Version Policy' : 'Add OS Version Policy'}
          onClose={() => setForm(null)}
          footer={
            <>
              <button onClick={() => setForm(null)} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700">Cancel</button>
              <button onClick={save} disabled={saving} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                {saving ? 'Saving…' : editing ? 'Save Changes' : 'Add Policy'}
              </button>
            </>
          }
        >
          <div className="space-y-4">
            {formErr && <div className="text-sm text-red-600 dark:text-red-400">{formErr}</div>}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Platform</label>
              <input
                list="os-platform-options"
                className={inputCls}
                value={form.platform}
                onChange={(e) => setForm({ ...form, platform: e.target.value })}
                placeholder="e.g. ios_xe"
              />
              <datalist id="os-platform-options">
                {platformOptions.map((p) => <option key={p} value={p} />)}
              </datalist>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Version Pattern</label>
              <input
                className={inputCls}
                value={form.version_pattern}
                onChange={(e) => setForm({ ...form, version_pattern: e.target.value })}
                placeholder={form.is_regex ? 'e.g. 17\\.12\\..*' : 'e.g. 17.12.4'}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input type="checkbox" checked={form.is_regex} onChange={(e) => setForm({ ...form, is_regex: e.target.checked })} />
              Treat pattern as a regular expression
            </label>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Status</label>
              <select className={inputCls} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as OSPolicyStatus })}>
                {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Notes</label>
              <textarea className={inputCls} rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal
          title="Delete OS Version Policy"
          onClose={() => setDeleting(null)}
          footer={
            <>
              <button onClick={() => setDeleting(null)} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700">Cancel</button>
              <button onClick={confirmDelete} className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium">Delete</button>
            </>
          }
        >
          <p className="text-sm text-gray-600 dark:text-gray-300">
            Delete the policy <span className="font-mono">{deleting.platform} {deleting.version_pattern}</span>?
          </p>
        </Modal>
      )}
    </div>
  )
}

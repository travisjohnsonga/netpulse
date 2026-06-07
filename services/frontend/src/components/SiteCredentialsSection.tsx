import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchSiteCredentials, addSiteCredential, deleteSiteCredential,
  fetchCredentials, fetchDeviceRoles,
  type SiteCredential, type CredentialProfileListItem, type DeviceRole,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

export default function SiteCredentialsSection({ siteId }: { siteId: number }) {
  const [rows, setRows] = useState<SiteCredential[]>([])
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<{ credential_profile: string; role: string; priority: number }>({ credential_profile: '', role: '', priority: 100 })
  const [busy, setBusy] = useState(false)

  const load = () => fetchSiteCredentials(siteId).then(setRows).catch((e) => setError(parseApiErrors(e, 'Failed to load credentials.')))
  useEffect(() => {
    load()
    fetchCredentials().then(setProfiles).catch(() => {})
    fetchDeviceRoles().then(setRoles).catch(() => {})
  }, [siteId])

  const save = async () => {
    if (!draft.credential_profile) { setError('Select a credential profile.'); return }
    setBusy(true); setError(null)
    try {
      await addSiteCredential(siteId, {
        credential_profile: Number(draft.credential_profile),
        role: draft.role ? Number(draft.role) : null,
        priority: draft.priority,
      })
      setAdding(false); setDraft({ credential_profile: '', role: '', priority: 100 }); load()
    } catch (e) { setError(parseApiErrors(e, 'Failed to add credential.')) }
    finally { setBusy(false) }
  }

  const remove = async (c: SiteCredential) => {
    try { await deleteSiteCredential(siteId, c.id); load() } catch (e) { setError(parseApiErrors(e, 'Delete failed.')) }
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Credential Profiles</h3>
        <button onClick={() => { setError(null); setAdding(true) }} className="px-2.5 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-md">+ Add</button>
      </div>
      {error && <div className="mb-2 text-sm text-red-600 dark:text-red-400 whitespace-pre-line">{error}</div>}
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Devices added to this site auto-inherit the matching credential profile (role-specific rules win; lower priority first).</p>
      {rows.length === 0 ? (
        <p className="text-sm text-gray-400 py-2">None assigned.</p>
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-100 dark:border-gray-700">
            <th className="py-1.5 font-medium">Credential</th><th className="py-1.5 font-medium">Role</th>
            <th className="py-1.5 font-medium">Priority</th><th className="py-1.5 font-medium text-right"></th>
          </tr></thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {rows.map((c) => (
              <tr key={c.id} className="text-gray-700 dark:text-gray-300">
                <td className="py-1.5">{c.credential_profile_name}</td>
                <td className="py-1.5">{c.role_name || <span className="text-gray-400">All roles</span>}</td>
                <td className="py-1.5">{c.priority}</td>
                <td className="py-1.5 text-right"><button onClick={() => remove(c)} className="text-xs text-red-600 dark:text-red-400 hover:underline">Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {adding && (
        <Modal title="Add Credential Profile" onClose={() => setAdding(false)}
          footer={<>
            <button onClick={() => setAdding(false)} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
            <button onClick={save} disabled={busy} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Adding…' : 'Add'}</button>
          </>}>
          <div className="space-y-3">
            <div><label className={label}>Credential Profile</label>
              <select className={input} value={draft.credential_profile} onChange={(e) => setDraft({ ...draft, credential_profile: e.target.value })}>
                <option value="">— Select —</option>
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div><label className={label}>Role</label>
              <select className={input} value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
                <option value="">— All roles —</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div><label className={label}>Priority</label>
              <input type="number" className={input} value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} />
              <p className="text-xs text-gray-400 mt-0.5">Lower = higher priority. More specific rules win.</p>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

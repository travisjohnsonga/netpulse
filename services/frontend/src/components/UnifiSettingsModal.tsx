import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchUnifiControllers, createUnifiController, updateUnifiController, deleteUnifiController,
  testUnifiController, syncUnifiController, syncAllUnifi, fetchSites,
  type UnifiController, type Site,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

type Draft = Partial<UnifiController> & { password?: string }

const BLANK: Draft = {
  name: '', host: '', port: 8443, username: '', unifi_site_id: 'default',
  site: null, verify_ssl: false, enabled: true, password: '',
}

export default function UnifiSettingsModal({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<UnifiController[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [editing, setEditing] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)

  const load = () => fetchUnifiControllers().then(setList).catch((e) => setError(parseApiErrors(e, 'Failed to load controllers.')))
  useEffect(() => { load(); fetchSites().then(setSites).catch(() => {}) }, [])

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 4000) }

  const saveDraft = async (): Promise<UnifiController | null> => {
    if (!editing) return null
    const { id, ...payload } = editing
    return id ? updateUnifiController(id, payload) : createUnifiController(payload)
  }

  const save = async () => {
    setBusy(true); setError(null); setTestResult(null)
    try { await saveDraft(); setEditing(null); flash('Controller saved.'); load() }
    catch (e) { setError(parseApiErrors(e, 'Failed to save controller.')) }
    finally { setBusy(false) }
  }

  const test = async () => {
    setBusy(true); setError(null); setTestResult(null)
    try {
      const saved = await saveDraft()              // persist so the test uses current values
      if (!saved) return
      setEditing({ ...editing, id: saved.id, password: '' })
      const r = await testUnifiController(saved.id, editing?.password)
      if (r.connected) setTestResult(`✅ Connected — ${r.device_count} device(s). Sites: ${(r.sites || []).join(', ') || 'default'}`)
      else setTestResult(`❌ ${r.error || 'Connection failed'}`)
      load()
    } catch (e) { setTestResult(`❌ ${parseApiErrors(e, 'Connection failed')}`) }
    finally { setBusy(false) }
  }

  const syncOne = async (c: UnifiController) => {
    setBusy(true); setError(null)
    try { const r = await syncUnifiController(c.id); flash(`Synced ${c.name}: ${r.imported} imported, ${r.updated} updated, ${r.skipped} skipped.`); load() }
    catch (e) { setError(parseApiErrors(e, `Sync failed for ${c.name}.`)) }
    finally { setBusy(false) }
  }

  const syncAll = async () => {
    setBusy(true); setError(null)
    try { const r = await syncAllUnifi(); flash(`Synced ${r.controllers} controller(s): ${r.imported} imported, ${r.updated} updated, ${r.failed} failed.`); load() }
    catch (e) { setError(parseApiErrors(e, 'Sync-all failed.')) }
    finally { setBusy(false) }
  }

  const remove = async (c: UnifiController) => {
    if (!window.confirm(`Delete controller "${c.name}"?`)) return
    try { await deleteUnifiController(c.id); load() } catch (e) { setError(parseApiErrors(e, 'Delete failed.')) }
  }

  // ── Add/Edit form ──────────────────────────────────────────────────────────
  if (editing) {
    const d = editing
    const set = (p: Partial<Draft>) => setEditing({ ...d, ...p })
    return (
      <Modal title={d.id ? `Edit ${d.name}` : 'Add UniFi Controller'} onClose={() => setEditing(null)}
        footer={
          <>
            <button onClick={() => setEditing(null)} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
            <button onClick={save} disabled={busy || !d.name || !d.host} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Saving…' : 'Save'}</button>
          </>
        }>
        <div className="space-y-3">
          {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
          {testResult && <div className="bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200">{testResult}</div>}
          <div><label className={label}>Name</label><input className={input} value={d.name || ''} onChange={(e) => set({ name: e.target.value })} placeholder="HQ Controller" /></div>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2"><label className={label}>Host (IP or hostname)</label><input className={input} value={d.host || ''} onChange={(e) => set({ host: e.target.value })} placeholder="192.168.1.1" /></div>
            <div><label className={label}>Port</label><input type="number" className={input} value={d.port ?? 8443} onChange={(e) => set({ port: Number(e.target.value) })} /></div>
          </div>
          <div><label className={label}>Username</label><input className={input} value={d.username || ''} onChange={(e) => set({ username: e.target.value })} autoComplete="off" /></div>
          <div>
            <label className={label}>Password {d.password_set && <span className="text-xs text-gray-400">(stored — leave blank to keep)</span>}</label>
            <input type="password" className={input} value={d.password || ''} onChange={(e) => set({ password: e.target.value })} placeholder={d.password_set ? '••••••••' : ''} autoComplete="new-password" />
          </div>
          <div>
            <label className={label}>UniFi Site ID</label>
            <input className={input} value={d.unifi_site_id || 'default'} onChange={(e) => set({ unifi_site_id: e.target.value })} />
            <p className="text-xs text-gray-400 mt-0.5">Find in UniFi → Settings → System → Advanced → Site ID.</p>
          </div>
          <div>
            <label className={label}>Assign to Site (NetPulse)</label>
            <select className={input} value={d.site ?? ''} onChange={(e) => set({ site: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— None —</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" checked={!!d.verify_ssl} onChange={(e) => set({ verify_ssl: e.target.checked })} /> Verify SSL</label>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200"><input type="checkbox" checked={d.enabled ?? true} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
          </div>
          <button onClick={test} disabled={busy || !d.host} className="w-full py-2 text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg disabled:opacity-50 dark:text-gray-200">Test Connection</button>
        </div>
      </Modal>
    )
  }

  // ── Controller list ──────────────────────────────────────────────────────────
  return (
    <Modal title="UniFi Controllers" onClose={onClose}
      footer={<button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>}>
      <div className="space-y-3">
        {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
        {msg && <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-900 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-300">{msg}</div>}
        <div className="flex items-center justify-between">
          <button onClick={syncAll} disabled={busy || list.length === 0} className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-300">Sync All</button>
          <button onClick={() => { setError(null); setTestResult(null); setEditing({ ...BLANK }) }} className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg">+ Add Controller</button>
        </div>
        {list.length === 0 ? (
          <p className="text-center text-sm text-gray-400 py-6">No controllers yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead><tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                <th className="px-3 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Host</th>
                <th className="px-3 py-2 font-medium">Site</th><th className="px-3 py-2 font-medium">Devices</th>
                <th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2 font-medium text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {list.map((c) => (
                  <tr key={c.id} className="text-gray-700 dark:text-gray-300">
                    <td className="px-3 py-2 font-medium">{c.name}</td>
                    <td className="px-3 py-2 font-mono text-xs">{c.host}:{c.port}</td>
                    <td className="px-3 py-2">{c.site_name || '—'}</td>
                    <td className="px-3 py-2">{c.device_count}</td>
                    <td className="px-3 py-2">{!c.enabled ? <span className="text-gray-400">Disabled</span> : c.last_error ? <span title={c.last_error} className="text-red-600 dark:text-red-400">❌ Error</span> : c.last_sync ? <span className="text-green-600 dark:text-green-400">✅ OK</span> : <span className="text-gray-400">Never synced</span>}</td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-1.5">
                        <button onClick={() => syncOne(c)} disabled={busy} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">Sync</button>
                        <button onClick={() => { setError(null); setTestResult(null); setEditing({ ...c, password: '' }) }} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">Edit</button>
                        <button onClick={() => remove(c)} className="px-2 py-0.5 text-xs border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  )
}

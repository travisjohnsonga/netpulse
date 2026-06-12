import { useEffect, useState } from 'react'
import Modal from './Modal'
import { Link } from 'react-router-dom'
import {
  fetchUnifiControllers, createUnifiController, updateUnifiController, deleteUnifiController,
  testUnifiController, syncUnifiController, syncAllUnifi, fetchSites, fetchCredentials,
  fetchUnifiCloud, saveUnifiCloud, testUnifiCloud, discoverUnifiControllers,
  type UnifiController, type Site, type UnifiCloudAccount, type UnifiDiscoveredController,
  type CredentialProfileListItem,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

function relTime(iso: string | null): string {
  if (!iso) return 'Never'
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'Just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

// Connection state → colored dot. Green = synced OK, red = error, grey =
// disabled or never synced.
function statusDot(c: UnifiController): { cls: string; title: string } {
  if (!c.enabled) return { cls: 'bg-gray-400', title: 'Disabled' }
  if (c.last_error) return { cls: 'bg-red-500', title: c.last_error }
  if (c.last_sync) return { cls: 'bg-green-500', title: 'Connected' }
  return { cls: 'bg-gray-300 dark:bg-gray-600', title: 'Never synced' }
}

type Draft = Partial<UnifiController>

const BLANK: Draft = {
  name: '', host: '', port: 8443, unifi_site_id: 'default',
  site: null, verify_ssl: false, enabled: true, credential_profile: null,
}

// Sort HTTPS-capable profiles first — they're the right fit for UniFi.
function sortProfiles(ps: CredentialProfileListItem[]): CredentialProfileListItem[] {
  return [...ps].sort((a, b) => {
    const ah = a.enabled_protocols.includes('https') ? 0 : 1
    const bh = b.enabled_protocols.includes('https') ? 0 : 1
    return ah - bh || a.name.localeCompare(b.name)
  })
}

export default function UnifiSettingsModal({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<UnifiController[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [editing, setEditing] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)

  const [cloud, setCloud] = useState<UnifiCloudAccount | null>(null)
  const [cloudKey, setCloudKey] = useState('')
  const [cloudMsg, setCloudMsg] = useState<string | null>(null)
  const [discovered, setDiscovered] = useState<UnifiDiscoveredController[] | null>(null)
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])

  const load = () => fetchUnifiControllers().then(setList).catch((e) => setError(parseApiErrors(e, 'Failed to load controllers.')))
  const loadCloud = () => fetchUnifiCloud().then(setCloud).catch(() => {})
  useEffect(() => {
    load(); loadCloud()
    fetchSites().then(setSites).catch(() => {})
    fetchCredentials().then((p) => setProfiles(sortProfiles(p))).catch(() => {})
  }, [])

  const saveCloud = async () => {
    setBusy(true); setCloudMsg(null); setError(null)
    try { await saveUnifiCloud({ api_key: cloudKey || undefined, enabled: true }); setCloudKey(''); setCloudMsg('API key saved.'); loadCloud() }
    catch (e) { setError(parseApiErrors(e, 'Failed to save API key.')) }
    finally { setBusy(false) }
  }
  const testCloud = async () => {
    setBusy(true); setCloudMsg(null); setError(null)
    try {
      if (cloudKey) await saveUnifiCloud({ api_key: cloudKey })
      const r = await testUnifiCloud(cloudKey || undefined)
      setCloudKey(''); loadCloud()
      setCloudMsg(r.connected ? `✅ Connected · ${r.host_count} host(s) found` : `❌ ${r.error || 'Connection failed'}`)
    } catch (e) { setCloudMsg(`❌ ${parseApiErrors(e, 'Connection failed')}`) }
    finally { setBusy(false) }
  }
  const discover = async () => {
    setBusy(true); setCloudMsg(null); setError(null); setDiscovered(null)
    try {
      if (cloudKey) await saveUnifiCloud({ api_key: cloudKey })
      const r = await discoverUnifiControllers()
      setCloudKey(''); setDiscovered(r.controllers); loadCloud(); load()
      setCloudMsg(`Found ${r.discovered} controller(s). Assign a credential profile to each to enable device sync.`)
    } catch (e) { setError(parseApiErrors(e, 'Discovery failed.')) }
    finally { setBusy(false) }
  }

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
      setEditing({ ...editing, id: saved.id })
      const r = await testUnifiController(saved.id, editing?.credential_profile ?? undefined)
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
          <div>
            <label className={label}>Credential Profile</label>
            {profiles.length === 0 ? (
              <div className="rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                ⚠️ No credential profiles found. Create one in Settings → Credentials before adding a controller.{' '}
                <Link to="/settings/credentials" className="underline font-medium" onClick={onClose}>Go to Credentials →</Link>
              </div>
            ) : (
              <>
                <select className={input} value={d.credential_profile ?? ''} onChange={(e) => set({ credential_profile: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">— Select a credential profile —</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}{p.enabled_protocols.includes('https') ? ' · HTTPS' : ` · ${p.enabled_protocols.join('/') || 'no protocols'}`}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-400 mt-1">
                  ℹ️ Pick a profile with <strong>HTTPS</strong> credentials whose username/password match a UniFi
                  controller <strong>local admin</strong> account (separate from your UI.com cloud API key). Recommended:
                  create a read-only local admin in UniFi Network → Admins &amp; Users → Add Admin (Local Access Only),
                  then add a spane credential profile with HTTPS enabled under Settings → Credentials.
                </p>
              </>
            )}
          </div>
          <div>
            <label className={label}>UniFi Site ID</label>
            <input className={input} value={d.unifi_site_id || 'default'} onChange={(e) => set({ unifi_site_id: e.target.value })} />
            <p className="text-xs text-gray-400 mt-0.5">Find in UniFi → Settings → System → Advanced → Site ID.</p>
          </div>
          <div>
            <label className={label}>Assign to Site (spane)</label>
            <select className={input} value={d.site ?? ''} onChange={(e) => set({ site: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— None —</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" checked={!!d.verify_ssl} onChange={(e) => set({ verify_ssl: e.target.checked })} /> Verify SSL</label>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200"><input type="checkbox" checked={d.enabled ?? true} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
          </div>
          <button onClick={test} disabled={busy || !d.host || !d.credential_profile} className="w-full py-2 text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg disabled:opacity-50 dark:text-gray-200">
            Test Connection{d.credential_profile ? ` (profile: ${profiles.find((p) => p.id === d.credential_profile)?.name ?? d.credential_profile})` : ''}
          </button>
        </div>
      </Modal>
    )
  }

  // ── Controller list ──────────────────────────────────────────────────────────
  return (
    <Modal title="UniFi Controllers" onClose={onClose} size="xl"
      footer={<button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>}>
      <div className="space-y-3">
        {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
        {msg && <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-900 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-300">{msg}</div>}

        {/* UniFi Cloud (Site Manager) account */}
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2 bg-gray-50/50 dark:bg-gray-900/30">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">UniFi Cloud Account</h4>
            {cloud?.api_key_set && <span className="text-xs text-green-600 dark:text-green-400">✅ Key stored · {cloud.host_count} host(s)</span>}
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">Generate an API key at <span className="font-mono">unifi.ui.com → Account → API Keys</span>. One key discovers all your controllers automatically.</p>
          <div className="flex gap-2">
            <input type="password" autoComplete="new-password" value={cloudKey} onChange={(e) => setCloudKey(e.target.value)}
              placeholder={cloud?.api_key_set ? '•••••••• (leave blank to keep)' : 'X-API-Key'}
              className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-900 dark:text-gray-100" />
            <button onClick={saveCloud} disabled={busy || !cloudKey} className="shrink-0 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-200">Save</button>
          </div>
          <div className="flex gap-2">
            <button onClick={testCloud} disabled={busy} className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-300">Test Connection</button>
            <button onClick={discover} disabled={busy} className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50">Discover Controllers</button>
          </div>
          {cloudMsg && <div className="text-xs text-gray-700 dark:text-gray-200">{cloudMsg}</div>}
          {discovered && discovered.length > 0 && (
            <div className="text-xs space-y-0.5 border-t border-gray-200 dark:border-gray-700 pt-2">
              {discovered.map((c, i) => (
                <div key={i}>{c.status === 'created' ? '✅' : '🔄'} {c.name} ({c.host}) → {c.status === 'created' ? 'Created' : 'Updated'}</div>
              ))}
            </div>
          )}
        </div>

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
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Address</th>
                <th className="px-3 py-2 font-medium text-right">Devices</th>
                <th className="px-3 py-2 font-medium">Last Sync</th>
                <th className="px-3 py-2 font-medium text-right sticky right-0 bg-gray-50 dark:bg-gray-900/50">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {list.map((c) => {
                  const dot = statusDot(c)
                  const sub = [c.model, c.version && `v${c.version}`, c.site_name].filter(Boolean).join(' · ')
                  return (
                    <tr key={c.id} className="text-gray-700 dark:text-gray-300">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span title={dot.title} className={`shrink-0 w-2 h-2 rounded-full ${dot.cls}`} />
                          <div className="min-w-0">
                            <div className="font-medium truncate">{c.name}</div>
                            {sub && <div className="text-xs text-gray-400 dark:text-gray-500 truncate">{sub}</div>}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">{c.host}:{c.port}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{c.device_count}</td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-500 dark:text-gray-400" title={c.last_sync || undefined}>{relTime(c.last_sync)}</td>
                      <td className="px-3 py-2 sticky right-0 bg-white dark:bg-gray-800">
                        <div className="flex items-center justify-end gap-1.5">
                          <button onClick={() => syncOne(c)} disabled={busy} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">Sync</button>
                          <button onClick={() => { setError(null); setTestResult(null); setEditing({ ...c }) }} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">Edit</button>
                          <button onClick={() => remove(c)} className="px-2 py-0.5 text-xs border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  )
}

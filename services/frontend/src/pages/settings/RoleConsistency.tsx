import { useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import { SectionHeader } from '../Settings'
import { parseApiErrors } from '../../api/errors'
import {
  fetchRoleRules, createRoleRule, updateRoleRule, deleteRoleRule, runRoleRule,
  fetchDeviceRoles, fetchSites,
  type RoleConsistencyRule, type RoleRunResult, type DeviceRole, type Site,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

const CHECK_TYPES = [
  { value: 'vlan_consistency', label: 'VLAN Consistency' },
  { value: 'ntp_consistency', label: 'NTP Server Consistency' },
  { value: 'dns_consistency', label: 'DNS Server Consistency' },
  { value: 'snmp_consistency', label: 'SNMP Community Consistency' },
  { value: 'aaa_consistency', label: 'AAA/RADIUS Consistency' },
  { value: 'banner_consistency', label: 'Login Banner Consistency' },
]
const SEVERITIES = ['error', 'warning', 'info']

const BLANK: Partial<RoleConsistencyRule> = {
  name: '', description: '', check_type: 'vlan_consistency', role: null, platform: '',
  site: null, excluded_vlans: [1], severity: 'warning', enabled: true,
}

function scopeText(r: RoleConsistencyRule): string {
  return [r.role_name, r.platform, r.site_name].filter(Boolean).join(' · ') || 'All devices'
}

export default function RoleConsistency() {
  const [rules, setRules] = useState<RoleConsistencyRule[]>([])
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [editing, setEditing] = useState<Partial<RoleConsistencyRule> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<RoleRunResult | null>(null)

  const load = () => fetchRoleRules().then(setRules).catch((e) => setError(parseApiErrors(e, 'Failed to load rules.')))
  useEffect(() => {
    load()
    fetchDeviceRoles().then(setRoles).catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])

  const save = async () => {
    if (!editing) return
    setBusy(true); setError(null)
    try {
      const { id, ...payload } = editing
      if (id) await updateRoleRule(id, payload); else await createRoleRule(payload)
      setEditing(null); load()
    } catch (e) { setError(parseApiErrors(e, 'Failed to save rule.')) }
    finally { setBusy(false) }
  }
  const remove = async (r: RoleConsistencyRule) => {
    if (!window.confirm(`Delete rule "${r.name}"?`)) return
    try { await deleteRoleRule(r.id); load() } catch (e) { setError(parseApiErrors(e, 'Delete failed.')) }
  }
  const run = async (r: RoleConsistencyRule) => {
    setBusy(true); setError(null)
    try { setResults(await runRoleRule(r.id)); load() }
    catch (e) { setError(parseApiErrors(e, 'Run failed.')) }
    finally { setBusy(false) }
  }
  const toggle = async (r: RoleConsistencyRule) => {
    try { await updateRoleRule(r.id, { enabled: !r.enabled }); load() } catch { /* ignore */ }
  }

  // ── Add/Edit form ───────────────────────────────────────────────────────────
  if (editing) {
    const d = editing
    const set = (p: Partial<RoleConsistencyRule>) => setEditing({ ...d, ...p })
    return (
      <Modal title={d.id ? `Edit ${d.name}` : 'Add Consistency Rule'} onClose={() => setEditing(null)} size="lg"
        footer={
          <>
            <button onClick={() => setEditing(null)} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
            <button onClick={save} disabled={busy || !d.name} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Saving…' : 'Save'}</button>
          </>
        }>
        <div className="space-y-3">
          {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
          <div><label className={label}>Name</label><input className={inputCls} value={d.name || ''} onChange={(e) => set({ name: e.target.value })} placeholder="Access Switch VLAN Consistency" /></div>
          <div><label className={label}>Description</label><textarea className={inputCls} rows={2} value={d.description || ''} onChange={(e) => set({ description: e.target.value })} /></div>
          <div><label className={label}>Check Type</label>
            <select className={inputCls} value={d.check_type} onChange={(e) => set({ check_type: e.target.value })}>
              {CHECK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div><label className={label}>Role</label>
              <select className={inputCls} value={d.role ?? ''} onChange={(e) => set({ role: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Any</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div><label className={label}>Platform</label><input className={inputCls} value={d.platform || ''} onChange={(e) => set({ platform: e.target.value })} placeholder="aos_cx" /></div>
            <div><label className={label}>Site</label>
              <select className={inputCls} value={d.site ?? ''} onChange={(e) => set({ site: e.target.value ? Number(e.target.value) : null })}>
                <option value="">All</option>
                {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
          </div>
          {d.check_type === 'vlan_consistency' && (
            <div><label className={label}>Excluded VLANs (comma-separated)</label>
              <input className={inputCls} value={(d.excluded_vlans || []).join(',')}
                onChange={(e) => set({ excluded_vlans: e.target.value.split(',').map((x) => parseInt(x.trim(), 10)).filter((n) => !isNaN(n)) })}
                placeholder="1, 999" />
              <p className="text-xs text-gray-400 mt-0.5">VLANs that legitimately differ per switch (e.g. management).</p>
            </div>
          )}
          <div className="flex items-center gap-6">
            <div><label className={label}>Severity</label>
              <select className={inputCls} value={d.severity} onChange={(e) => set({ severity: e.target.value })}>
                {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-200 mt-5"><input type="checkbox" checked={d.enabled ?? true} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
          </div>
        </div>
      </Modal>
    )
  }

  // ── List ────────────────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeader title="Role Consistency" description="Compare config (VLANs, NTP, DNS…) across devices of the same role and flag drift." />
      {error && <div className="mb-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
      <div className="flex justify-end mb-3">
        <button onClick={() => { setError(null); setEditing({ ...BLANK }) }} className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg">+ Add Rule</button>
      </div>
      {rules.length === 0 ? (
        <p className="text-center text-sm text-gray-400 py-10">No consistency rules yet. Add one or enable a seeded example.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
          <table className="w-full text-sm">
            <thead><tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
              <th className="px-3 py-2 font-medium">Rule</th>
              <th className="px-3 py-2 font-medium">Scope</th>
              <th className="px-3 py-2 font-medium text-center">Result</th>
              <th className="px-3 py-2 font-medium text-right">Actions</th>
            </tr></thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rules.map((r) => {
                const s = r.last_summary && 'status' in r.last_summary ? r.last_summary : null
                return (
                  <tr key={r.id} className="text-gray-700 dark:text-gray-300">
                    <td className="px-3 py-2">
                      <div className="font-medium text-gray-900 dark:text-gray-100">{r.name}{!r.enabled && <span className="ml-2 text-[11px] text-gray-400">(disabled)</span>}</div>
                      <div className="text-xs text-gray-400">{r.check_type_display || r.check_type}</div>
                    </td>
                    <td className="px-3 py-2 text-xs">{scopeText(r)}</td>
                    <td className="px-3 py-2 text-center">
                      {s && s.status === 'complete' ? (
                        <span className="whitespace-nowrap"><span className="text-green-600">✅ {s.passing}</span> <span className="text-red-600">⚠️ {s.failing}</span></span>
                      ) : s && s.status === 'skip' ? <span className="text-xs text-gray-400">skipped</span> : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-1.5">
                        <button onClick={() => run(r)} disabled={busy} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">Run</button>
                        <button onClick={() => toggle(r)} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">{r.enabled ? 'Disable' : 'Enable'}</button>
                        <button onClick={() => { setError(null); setEditing({ ...r }) }} className="px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">Edit</button>
                        <button onClick={() => remove(r)} className="px-2 py-0.5 text-xs border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {results && (
        <Modal title={`${results.rule} — Results`} onClose={() => setResults(null)} size="xl"
          footer={<button onClick={() => setResults(null)} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>}>
          {results.status === 'skip' ? (
            <p className="text-sm text-gray-500">Skipped: {results.reason}</p>
          ) : (
            <>
              <div className="text-sm mb-3">
                <p className="mb-1"><span className="text-green-600 font-medium">{results.passing} passing</span> / <span className="text-red-600 font-medium">{results.failing} failing</span> ({results.total_devices} devices)</p>
                <p className="text-xs text-gray-500">Expected (majority of devices): <span className="font-mono">{(results.expected || []).join(', ') || '—'}</span></p>
              </div>
              <div className="space-y-1.5">
                {(results.results || []).map((row, i) => (
                  <div key={i} className={clsx('rounded-lg border p-2 text-sm', row.status === 'pass' ? 'border-green-200 dark:border-green-900' : 'border-red-200 dark:border-red-900')}>
                    <div className="font-medium">{row.status === 'pass' ? '✅' : '❌'} {row.device}{row.status === 'pass' ? ' — all match' : ''}</div>
                    {row.missing.length > 0 && <div className="text-xs text-red-600">Missing: {row.missing.join(', ')}</div>}
                    {row.extra.length > 0 && <div className="text-xs text-amber-600">Extra: {row.extra.join(', ')}</div>}
                    {row.remediation && <pre className="mt-1 text-[11px] bg-gray-50 dark:bg-gray-900 rounded p-1.5 text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{row.remediation}</pre>}
                  </div>
                ))}
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import { SectionHeader } from '../Settings'
import { parseApiErrors } from '../../api/errors'
import {
  fetchInterfaceRules, createInterfaceRule, updateInterfaceRule, deleteInterfaceRule,
  runInterfaceRule,
  type InterfaceComplianceRule, type InterfaceCheck, type InterfaceRunResult,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

const TRIGGERS = [
  { value: 'lldp_capability', label: 'LLDP Capability' },
  { value: 'lldp_neighbor_platform', label: 'LLDP Neighbor Platform' },
  { value: 'lldp_neighbor_role', label: 'LLDP Neighbor Role' },
  { value: 'interface_description', label: 'Interface Description' },
  { value: 'manual', label: 'Manual Tag' },
]
// LLDP capability values + friendly names (wlan-access-point catches all AP vendors).
const CAPABILITIES = [
  { value: 'wlan-access-point', label: '📶 Wireless AP' },
  { value: 'telephone', label: '📞 IP Phone' },
  { value: 'bridge', label: '🔀 Switch/Bridge' },
  { value: 'router', label: '🔀 Router' },
  { value: 'station', label: '💻 Workstation/Server' },
  { value: 'repeater', label: '📡 Repeater/Hub' },
  { value: 'other', label: 'Other Device' },
]
const CAP_LABEL: Record<string, string> = Object.fromEntries(CAPABILITIES.map((c) => [c.value, c.label]))
const CHECK_TYPES = [
  { value: 'config_contains', label: 'Config Contains' },
  { value: 'config_not_contains', label: 'Config Does NOT Contain' },
  { value: 'vlan_check', label: 'VLAN Mode Check' },
]
const SEVERITIES = ['error', 'warning', 'info']

function triggerLabel(r: InterfaceComplianceRule): string {
  if (r.trigger === 'lldp_capability') {
    const cap = CAP_LABEL[r.trigger_value] || r.trigger_value
    return `${cap} (LLDP capability)`
  }
  return `${r.trigger_display || r.trigger}: ${r.trigger_value}`
}

const BLANK: Partial<InterfaceComplianceRule> = {
  name: '', description: '', trigger: 'lldp_capability', trigger_value: 'wlan-access-point',
  platform: '', enabled: true, checks: [],
}

export default function InterfaceRules() {
  const [rules, setRules] = useState<InterfaceComplianceRule[]>([])
  const [editing, setEditing] = useState<Partial<InterfaceComplianceRule> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<InterfaceRunResult | null>(null)

  const load = () => fetchInterfaceRules().then(setRules).catch((e) => setError(parseApiErrors(e, 'Failed to load rules.')))
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!editing) return
    setBusy(true); setError(null)
    try {
      const { id, ...payload } = editing
      if (id) await updateInterfaceRule(id, payload); else await createInterfaceRule(payload)
      setEditing(null); load()
    } catch (e) { setError(parseApiErrors(e, 'Failed to save rule.')) }
    finally { setBusy(false) }
  }
  const remove = async (r: InterfaceComplianceRule) => {
    if (!window.confirm(`Delete rule "${r.name}"?`)) return
    try { await deleteInterfaceRule(r.id); load() } catch (e) { setError(parseApiErrors(e, 'Delete failed.')) }
  }
  const run = async (r: InterfaceComplianceRule) => {
    setBusy(true); setError(null)
    try { setResults(await runInterfaceRule(r.id)); load() }
    catch (e) { setError(parseApiErrors(e, 'Run failed.')) }
    finally { setBusy(false) }
  }
  const toggle = async (r: InterfaceComplianceRule) => {
    try { await updateInterfaceRule(r.id, { enabled: !r.enabled }); load() } catch { /* ignore */ }
  }

  // ── Add/Edit form ───────────────────────────────────────────────────────────
  if (editing) {
    const d = editing
    const set = (p: Partial<InterfaceComplianceRule>) => setEditing({ ...d, ...p })
    const checks = d.checks || []
    const setCheck = (i: number, p: Partial<InterfaceCheck>) =>
      set({ checks: checks.map((c, j) => (j === i ? { ...c, ...p } : c)) })
    const addCheck = () => set({ checks: [...checks, { type: 'config_contains', value: '', severity: 'warning', description: '' }] })
    const delCheck = (i: number) => set({ checks: checks.filter((_, j) => j !== i) })
    return (
      <Modal title={d.id ? `Edit ${d.name}` : 'Add Interface Rule'} onClose={() => setEditing(null)} size="xl"
        footer={
          <>
            <button onClick={() => setEditing(null)} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
            <button onClick={save} disabled={busy || !d.name || !d.trigger_value} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Saving…' : 'Save'}</button>
          </>
        }>
        <div className="space-y-3">
          {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
          <div><label className={label}>Name</label><input className={inputCls} value={d.name || ''} onChange={(e) => set({ name: e.target.value })} placeholder="Wireless AP Port Config" /></div>
          <div><label className={label}>Description</label><textarea className={inputCls} rows={2} value={d.description || ''} onChange={(e) => set({ description: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className={label}>Trigger Type</label>
              <select className={inputCls} value={d.trigger} onChange={(e) => set({ trigger: e.target.value })}>
                {TRIGGERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Trigger Value</label>
              {d.trigger === 'lldp_capability' ? (
                <select className={inputCls} value={d.trigger_value} onChange={(e) => set({ trigger_value: e.target.value })}>
                  {CAPABILITIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              ) : (
                <input className={inputCls} value={d.trigger_value || ''} onChange={(e) => set({ trigger_value: e.target.value })}
                  placeholder={d.trigger === 'lldp_neighbor_platform' ? 'unifi_ap,mist_ap' : d.trigger === 'interface_description' ? '(?i)(cam|camera)' : 'value'} />
              )}
            </div>
          </div>
          {d.trigger === 'lldp_capability' && (
            <p className="text-xs text-gray-400">Matches any switch port whose LLDP neighbor advertises this capability. <strong>Wireless AP</strong> catches all APs regardless of vendor (UniFi, Mist, Cisco, Aruba…).</p>
          )}
          <div><label className={label}>Switch Platform filter (optional)</label><input className={inputCls} value={d.platform || ''} onChange={(e) => set({ platform: e.target.value })} placeholder="aos_cx (blank = any)" /></div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <span className={label}>Checks</span>
              <button onClick={addCheck} className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded">+ Add Check</button>
            </div>
            {checks.length === 0 ? <p className="text-xs text-gray-400 py-2">No checks yet.</p> : (
              <div className="space-y-2">
                {checks.map((c, i) => (
                  <div key={i} className="rounded-lg border border-gray-200 dark:border-gray-700 p-2 grid grid-cols-12 gap-2 items-end">
                    <div className="col-span-3"><label className="text-[11px] text-gray-400">Type</label>
                      <select className={inputCls} value={c.type} onChange={(e) => setCheck(i, { type: e.target.value })}>
                        {CHECK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="col-span-3"><label className="text-[11px] text-gray-400">{c.type === 'vlan_check' ? 'VLAN mode' : 'Value'}</label>
                      {c.type === 'vlan_check' ? (
                        <select className={inputCls} value={c.vlan_type || 'access'} onChange={(e) => setCheck(i, { vlan_type: e.target.value })}>
                          <option value="access">access</option><option value="trunk">trunk</option>
                        </select>
                      ) : (
                        <input className={inputCls} value={c.value || ''} onChange={(e) => setCheck(i, { value: e.target.value })} placeholder="spanning-tree" />
                      )}
                    </div>
                    <div className="col-span-2"><label className="text-[11px] text-gray-400">Severity</label>
                      <select className={inputCls} value={c.severity || 'warning'} onChange={(e) => setCheck(i, { severity: e.target.value })}>
                        {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </div>
                    <div className="col-span-3"><label className="text-[11px] text-gray-400">Description</label>
                      <input className={inputCls} value={c.description || ''} onChange={(e) => setCheck(i, { description: e.target.value })} placeholder="STP edge enabled" />
                    </div>
                    <div className="col-span-1"><button onClick={() => delCheck(i)} className="w-full py-2 text-red-600 hover:text-red-700 text-sm">✕</button></div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-200"><input type="checkbox" checked={d.enabled ?? true} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
        </div>
      </Modal>
    )
  }

  // ── List ────────────────────────────────────────────────────────────────────
  return (
    <div>
      <SectionHeader title="Interface Rules" description="LLDP-aware per-interface config checks — e.g. verify AP/phone/uplink ports are configured correctly." />
      {error && <div className="mb-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
      <div className="flex justify-end mb-3">
        <button onClick={() => { setError(null); setEditing({ ...BLANK }) }} className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg">+ Add Rule</button>
      </div>
      {rules.length === 0 ? (
        <p className="text-center text-sm text-gray-400 py-10">No interface rules yet. Add one or enable a seeded example.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
          <table className="w-full text-sm">
            <thead><tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
              <th className="px-3 py-2 font-medium">Rule</th>
              <th className="px-3 py-2 font-medium">Trigger</th>
              <th className="px-3 py-2 font-medium text-center">Result</th>
              <th className="px-3 py-2 font-medium text-right">Actions</th>
            </tr></thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rules.map((r) => {
                const s = r.result_summary
                return (
                  <tr key={r.id} className="text-gray-700 dark:text-gray-300">
                    <td className="px-3 py-2">
                      <div className="font-medium text-gray-900 dark:text-gray-100">{r.name}{!r.enabled && <span className="ml-2 text-[11px] text-gray-400">(disabled)</span>}</div>
                      {r.platform && <div className="text-xs text-gray-400">{r.platform}</div>}
                    </td>
                    <td className="px-3 py-2 text-xs">{triggerLabel(r)}</td>
                    <td className="px-3 py-2 text-center">
                      {s && s.total > 0 ? (
                        <span className="whitespace-nowrap"><span className="text-green-600">✅ {s.passing}</span> <span className="text-red-600">❌ {s.failing}</span></span>
                      ) : <span className="text-gray-300">—</span>}
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
          <p className="text-sm mb-3"><span className="text-green-600 font-medium">{results.summary.passing} passing</span> / <span className="text-red-600 font-medium">{results.summary.failing} failing</span> ({results.summary.matched} interfaces)</p>
          {results.results.length === 0 ? <p className="text-sm text-gray-400">No matching interfaces (need LLDP data + a config backup).</p> : (
            <div className="space-y-2">
              {results.results.map((row, i) => (
                <div key={i} className={clsx('rounded-lg border p-2', row.passed ? 'border-green-200 dark:border-green-900' : 'border-red-200 dark:border-red-900')}>
                  <div className="font-medium text-sm">{row.passed ? '✅' : '❌'} {row.switch} / {row.interface}</div>
                  {row.neighbor && <div className="text-xs text-gray-400">Connected to: {row.neighbor}</div>}
                  <div className="mt-1 space-y-0.5">
                    {row.checks.map((c, j) => (
                      <div key={j} className="text-xs">{c.passed ? '✅' : '❌'} {c.description || c.value}{!c.passed && <span className="text-red-500"> — {c.type === 'config_not_contains' ? 'PRESENT' : 'MISSING'}</span>}</div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}

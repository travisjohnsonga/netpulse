import { useEffect, useMemo, useState } from 'react'
import Modal from '../../components/Modal'
import { RoleDot } from '../../components/RoleBubble'
import { SectionHeader } from '../Settings'
import {
  fetchHostnameRules, createHostnameRule, updateHostnameRule, deleteHostnameRule,
  testHostnameRule, applyHostnameRulesBulk, fetchDeviceRoles, fetchSites,
  type HostnameRule, type HostnameRuleType, type DeviceRole, type Site,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const TYPE_LABELS: Record<HostnameRuleType, string> = {
  role: 'Role', site: 'Site', both: 'Role + Site',
}

export default function HostnameRules() {
  const [rules, setRules] = useState<HostnameRule[]>([])
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<HostnameRule | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<HostnameRule | null>(null)
  const [applyMsg, setApplyMsg] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)

  const load = () => {
    setLoading(true)
    Promise.all([fetchHostnameRules(), fetchDeviceRoles(), fetchSites()])
      .then(([r, ro, si]) => { setRules(r); setRoles(ro); setSites(si); setError(null) })
      .catch(() => setError('Failed to load hostname rules.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const runApply = async () => {
    setApplying(true); setApplyMsg(null)
    try {
      const res = await applyHostnameRulesBulk(false)
      setApplyMsg(`Updated ${res.updated} device${res.updated !== 1 ? 's' : ''}, skipped ${res.skipped}.`)
    } catch {
      setApplyMsg('Failed to apply rules.')
    } finally {
      setApplying(false)
    }
  }

  return (
    <div>
      <SectionHeader
        title="Hostname Rules"
        description="Auto-assign device role and site from the hostname during discovery, enrichment, or on demand. First match per type wins (lowest priority number)."
        action={
          <div className="flex gap-2">
            <button onClick={runApply} disabled={applying || rules.length === 0}
              className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 text-gray-700 dark:text-gray-300">
              {applying ? 'Applying…' : 'Apply Rules'}
            </button>
            <button onClick={() => setCreating(true)}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
              + Add Rule
            </button>
          </div>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}
      {applyMsg && <div className="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-blue-700 dark:text-blue-300 mb-4">{applyMsg}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : rules.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
            No hostname rules yet. Add a rule to auto-assign roles and sites from device hostnames.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium">Pattern</th>
                <th className="px-5 py-3 font-medium">Type</th>
                <th className="px-5 py-3 font-medium">Assigns</th>
                <th className="px-5 py-3 font-medium">Priority</th>
                <th className="px-5 py-3 font-medium">Active</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rules.map((r) => (
                <tr key={r.id} className={r.enabled ? '' : 'opacity-50'}>
                  <td className="px-5 py-3 text-gray-900 dark:text-gray-100">{r.name}</td>
                  <td className="px-5 py-3"><code className="text-xs bg-gray-100 dark:bg-gray-900 px-1.5 py-0.5 rounded text-gray-700 dark:text-gray-300">{r.pattern}</code></td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{TYPE_LABELS[r.rule_type]}</td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">
                    <div className="flex flex-col gap-0.5">
                      {r.role_name && (
                        <span className="inline-flex items-center gap-1.5">
                          <RoleDot color={r.role_color || '#6366f1'} />{r.role_name}
                        </span>
                      )}
                      {r.site_name && <span>📍 {r.site_name}</span>}
                      {!r.role_name && !r.site_name && <span className="text-gray-400">—</span>}
                    </div>
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.priority}</td>
                  <td className="px-5 py-3">{r.enabled ? '✅' : '—'}</td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setEditing(r)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 mr-1">Edit</button>
                    <button onClick={() => setDeleting(r)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(creating || editing) && (
        <RuleModal
          rule={editing}
          roles={roles}
          sites={sites}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      )}
      {deleting && (
        <DeleteRuleModal
          rule={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

function RuleModal({ rule, roles, sites, onClose, onSaved }: {
  rule: HostnameRule | null
  roles: DeviceRole[]
  sites: Site[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(rule?.name ?? '')
  const [pattern, setPattern] = useState(rule?.pattern ?? '')
  const [ruleType, setRuleType] = useState<HostnameRuleType>(rule?.rule_type ?? 'role')
  const [roleId, setRoleId] = useState<number | null>(rule?.role ?? null)
  const [siteId, setSiteId] = useState<number | null>(rule?.site ?? null)
  const [priority, setPriority] = useState(rule?.priority ?? 100)
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Pattern tester
  const [testHost, setTestHost] = useState('')
  const [testResult, setTestResult] = useState<{ hostname: string; matches: boolean } | null>(null)
  const [testing, setTesting] = useState(false)

  const showRole = ruleType === 'role' || ruleType === 'both'
  const showSite = ruleType === 'site' || ruleType === 'both'

  const assignLabel = useMemo(() => {
    const parts: string[] = []
    if (showRole && roleId) parts.push(roles.find((r) => r.id === roleId)?.name ?? '')
    if (showSite && siteId) parts.push(sites.find((s) => s.id === siteId)?.name ?? '')
    return parts.filter(Boolean).join(', ')
  }, [showRole, showSite, roleId, siteId, roles, sites])

  const runTest = async () => {
    if (!testHost.trim() || !pattern.trim()) return
    setTesting(true)
    try {
      const res = await testHostnameRule(pattern, [testHost.trim()])
      setTestResult(res[0] ?? null)
    } catch {
      setTestResult(null)
      setErr('Invalid regex pattern.')
    } finally {
      setTesting(false)
    }
  }

  const save = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    if (!pattern.trim()) { setErr('Pattern is required.'); return }
    setSaving(true); setErr(null)
    try {
      const payload = {
        name: name.trim(),
        pattern: pattern.trim(),
        rule_type: ruleType,
        role: showRole ? roleId : null,
        site: showSite ? siteId : null,
        priority,
        enabled,
      }
      if (rule) await updateHostnameRule(rule.id, payload)
      else await createHostnameRule(payload)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save rule.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={rule ? `Edit Rule: ${rule.name}` : 'Add Hostname Rule'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Core switches" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Pattern (regex)</label>
          <input className={`${inputCls} font-mono`} value={pattern} onChange={(e) => { setPattern(e.target.value); setTestResult(null) }} placeholder="e.g. -(crt|mdf|ddf)-" />
        </div>

        {/* Pattern tester */}
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3">
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Test hostname</label>
          <div className="flex gap-2">
            <input className={inputCls} value={testHost} onChange={(e) => setTestHost(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runTest() }} placeholder="wco2-mdf-crt-01" />
            <button onClick={runTest} disabled={testing || !pattern.trim() || !testHost.trim()}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50 text-gray-700 dark:text-gray-300 whitespace-nowrap">Test</button>
          </div>
          {testResult && (
            <div className="mt-2 text-sm">
              {testResult.matches ? (
                <span className="text-green-600 dark:text-green-400">
                  ✅ Matches{assignLabel ? ` — will assign: ${assignLabel}` : ''}
                </span>
              ) : (
                <span className="text-gray-500 dark:text-gray-400">❌ No match</span>
              )}
            </div>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Rule type</label>
          <select className={inputCls} value={ruleType} onChange={(e) => setRuleType(e.target.value as HostnameRuleType)}>
            <option value="role">Role</option>
            <option value="site">Site</option>
            <option value="both">Role + Site</option>
          </select>
        </div>
        {showRole && (
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Role</label>
            <select className={inputCls} value={roleId ?? ''} onChange={(e) => setRoleId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">— Select role —</option>
              {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </div>
        )}
        {showSite && (
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Site</label>
            <select className={inputCls} value={siteId ?? ''} onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">— Select site —</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        )}
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Priority (lower = first)</label>
            <input type="number" className={inputCls} value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
          </div>
          <div className="flex items-end pb-2">
            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded" />
              Enabled
            </label>
          </div>
        </div>
      </div>
    </Modal>
  )
}

function DeleteRuleModal({ rule, onClose, onDeleted }: {
  rule: HostnameRule
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const remove = async () => {
    setDeleting(true); setErr(null)
    try { await deleteHostnameRule(rule.id); onDeleted() }
    catch { setErr('Failed to delete rule.'); setDeleting(false) }
  }

  return (
    <Modal
      title="Delete Rule"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={remove} disabled={deleting} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{deleting ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <p className="text-sm text-gray-700 dark:text-gray-300">
          Delete the rule <strong>{rule.name}</strong>? This cannot be undone. Existing device assignments are not changed.
        </p>
      </div>
    </Modal>
  )
}

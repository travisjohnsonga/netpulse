import { useCallback, useEffect, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import {
  fetchAlertRules, createAlertRule, updateAlertRule, deleteAlertRule,
  type AlertRule, type AlertSeverity,
} from '../../api/client'
import { parseApiErrors } from '../../api/errors'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader, Tabs } from '../Settings'

// Only pure user-created rules are deletable. Tier-1 system rules (kind=system)
// and engine-fired built-ins (is_system — spane re-creates them by name) are
// protected: no delete button, disable instead.
const isDeletable = (r: AlertRule) => r.kind !== 'system' && !r.is_system

const TABS = [
  { id: 'rules', label: 'Alert Rules' },
  { id: 'windows', label: 'Maintenance Windows' },
  { id: 'templates', label: 'Templates' },
]

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const SEVERITY_BADGE: Record<AlertSeverity, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  info: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}

export default function Alerting() {
  const [tab, setTab] = useState('rules')
  return (
    <div>
      <SectionHeader title="Alerting" description="Alert rules, maintenance windows and notification templates." />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'rules' && <RulesTab />}
      {tab === 'windows' && <MaintenanceTab />}
      {tab === 'templates' && <TemplatesTab />}
    </div>
  )
}

// ── Alert Rules (live API) ───────────────────────────────────────────────────

function RulesTab() {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  // Rule pending a delete confirm (operational only).
  const [deleting, setDeleting] = useState<AlertRule | null>(null)
  // System rule pending a disable warning (Tier-1 self-health rules warn before
  // being turned off — they're load-bearing for spane's own reliability).
  const [disablingSystem, setDisablingSystem] = useState<AlertRule | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchAlertRules()
      .then((data) => { setRules(data); setError(null) })
      .catch(() => setError('Failed to load alert rules.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const setActive = async (rule: AlertRule, active: boolean) => {
    // optimistic
    setRules((rs) => rs.map((r) => (r.id === rule.id ? { ...r, is_active: active } : r)))
    try {
      await updateAlertRule(rule.id, { is_active: active })
    } catch {
      setRules((rs) => rs.map((r) => (r.id === rule.id ? { ...r, is_active: !active } : r)))
      setError('Failed to update rule.')
    }
  }

  // Enabled toggle. Friction scales with consequence: disabling a Tier-1 SYSTEM
  // rule warns first (spane would stop watching its own health); everything else
  // — including re-enabling a system rule — toggles immediately, no nag.
  const toggle = (rule: AlertRule) => {
    if (rule.kind === 'system' && rule.is_active) {
      setDisablingSystem(rule)
      return
    }
    void setActive(rule, !rule.is_active)
  }

  const confirmDelete = async () => {
    const rule = deleting
    if (!rule) return
    try {
      await deleteAlertRule(rule.id)
      setRules((rs) => rs.filter((r) => r.id !== rule.id))
      setDeleting(null)
    } catch (e) {
      setError(parseApiErrors(e, 'Failed to delete rule.'))
      setDeleting(null)
    }
  }

  // Notify toggle (separate from Enabled): off → the rule still generates
  // AlertEvents (UI) but never notifies (observe-only).
  const toggleNotify = async (rule: AlertRule) => {
    const next = !(rule.notify_enabled ?? true)
    setRules((rs) => rs.map((r) => (r.id === rule.id ? { ...r, notify_enabled: next } : r)))
    try {
      await updateAlertRule(rule.id, { notify_enabled: next })
    } catch {
      setRules((rs) => rs.map((r) => (r.id === rule.id ? { ...r, notify_enabled: !next } : r)))
      setError('Failed to update rule.')
    }
  }

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Add Rule</button>
      </div>
      {error && <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : rules.length === 0 ? (
          <EmptyState title="No alert rules" description="Create a rule to start generating alerts from telemetry and events." action={{ label: 'Add Rule', onClick: () => setAdding(true) }} icon="⚠️" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Cooldown</th>
                  <th className="px-5 py-3 font-medium">Channels</th>
                  <th className="px-5 py-3 font-medium text-right" title="Generate AlertEvents (UI) — the whole rule on/off">Enabled</th>
                  <th className="px-5 py-3 font-medium text-right" title="Send notifications (email/Teams). Off = observe-only: still shows in the UI, pages no one.">Notify</th>
                  <th className="px-5 py-3 font-medium text-right w-px"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {rules.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3">
                      <p className="font-medium text-gray-800 dark:text-gray-100 flex items-center gap-2">
                        {r.name}
                        {r.kind === 'system' ? (
                          <span
                            className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                            title="Tier 1 — spane platform self-health (its own machinery). Load-bearing for spane's reliability."
                          >
                            System
                          </span>
                        ) : (
                          <span
                            className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 dark:bg-gray-700/40 dark:text-gray-400"
                            title="Tier 2 — monitors your network/servers. You own this rule."
                          >
                            Operational
                          </span>
                        )}
                      </p>
                      {r.description && <p className="text-xs text-gray-500 dark:text-gray-400">{r.description}</p>}
                    </td>
                    <td className="px-5 py-3">
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', SEVERITY_BADGE[r.severity])}>{r.severity}</span>
                    </td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.cooldown_minutes}m</td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.channels.length}</td>
                    <td className="px-5 py-3 text-right">
                      <Toggle on={r.is_active} onClick={() => toggle(r)} />
                    </td>
                    <td className="px-5 py-3 text-right">
                      {/* Dimmed when the rule is disabled — Notify is moot if it doesn't generate. */}
                      <span className={clsx(!r.is_active && 'opacity-40')} title={!r.is_active ? 'Rule is disabled' : undefined}>
                        <Toggle on={r.notify_enabled ?? true} onClick={() => toggleNotify(r)} />
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right">
                      {/* Delete is offered ONLY for user-created rules. Both Tier-1
                          system rules AND engine-fired built-ins (is_system) are
                          protected — deleting a built-in is futile (the engine
                          re-creates it), so disable it instead. */}
                      {isDeletable(r) ? (
                        <button
                          onClick={() => setDeleting(r)}
                          className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30"
                          title={`Delete rule "${r.name}"`}
                          aria-label={`Delete rule ${r.name}`}
                        >
                          <TrashIcon />
                        </button>
                      ) : (
                        <span
                          className="text-gray-300 dark:text-gray-600 text-xs"
                          title={r.kind === 'system'
                            ? 'System rules monitor spane’s own health — disable them instead.'
                            : 'Built-in monitoring rule — spane re-creates it automatically. Disable it to stop its alerts.'}
                        >—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {adding && <AddRuleModal onClose={() => setAdding(false)} onCreated={() => { setAdding(false); load() }} onError={() => setError('Failed to create rule.')} />}

      {deleting && (
        <ConfirmDialog
          title="Delete alert rule?"
          confirmLabel="Delete rule"
          tone="danger"
          onConfirm={confirmDelete}
          onClose={() => setDeleting(null)}
        >
          <p className="text-sm text-gray-700 dark:text-gray-300">
            Delete rule <span className="font-semibold">“{deleting.name}”</span>? This can’t be undone.
          </p>
        </ConfirmDialog>
      )}

      {disablingSystem && (
        <ConfirmDialog
          title="Disable a system rule?"
          confirmLabel="Disable anyway"
          tone="warning"
          onConfirm={async () => { await setActive(disablingSystem, false); setDisablingSystem(null) }}
          onClose={() => setDisablingSystem(null)}
        >
          <p className="text-sm text-gray-700 dark:text-gray-300">
            <span className="font-semibold">“{disablingSystem.name}”</span> monitors spane’s own
            health — disabling it means spane can’t detect{' '}
            {disablingSystem.name === 'Notification Delivery Failed'
              ? 'notification-delivery failures'
              : 'this platform-health condition'}. Disable anyway?
          </p>
        </ConfirmDialog>
      )}
    </div>
  )
}

function TrashIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 7h12M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2m-7 0v12a1 1 0 001 1h6a1 1 0 001-1V7" />
    </svg>
  )
}

// Small confirm/warning dialog built on the shared Modal. `tone` colours the
// primary action: danger (red) for destructive deletes, warning (amber) for a
// reversible-but-consequential disable.
function ConfirmDialog({
  title, children, confirmLabel, tone, onConfirm, onClose,
}: {
  title: string
  children: ReactNode
  confirmLabel: string
  tone: 'danger' | 'warning'
  onConfirm: () => void | Promise<void>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { await onConfirm() } finally { setBusy(false) }
  }
  const confirmCls = tone === 'danger'
    ? 'bg-red-600 hover:bg-red-700'
    : 'bg-amber-600 hover:bg-amber-700'
  return (
    <Modal
      title={title}
      onClose={onClose}
      size="md"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={run} disabled={busy} className={clsx('flex-1 py-2.5 disabled:opacity-50 text-white rounded-lg text-sm font-medium', confirmCls)}>{busy ? 'Working…' : confirmLabel}</button>
        </>
      }
    >
      <div>{children}</div>
    </Modal>
  )
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={clsx('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', on ? 'bg-blue-600' : 'bg-gray-300')}
      aria-pressed={on}
    >
      <span className={clsx('inline-block h-4 w-4 transform rounded-full bg-white transition-transform', on ? 'translate-x-4' : 'translate-x-0.5')} />
    </button>
  )
}

function AddRuleModal({ onClose, onCreated, onError }: { onClose: () => void; onCreated: () => void; onError: () => void }) {
  const [name, setName] = useState('')
  const [severity, setSeverity] = useState<AlertSeverity>('medium')
  const [cooldown, setCooldown] = useState('60')
  const [condition, setCondition] = useState('{\n  "metric": "cpu",\n  "op": ">",\n  "threshold": 90\n}')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    let parsed: Record<string, unknown>
    try { parsed = JSON.parse(condition) } catch { setErr('Condition must be valid JSON.'); return }
    setSaving(true); setErr(null)
    try {
      await createAlertRule({ name: name.trim(), severity, cooldown_minutes: Number(cooldown) || 60, condition: parsed, is_active: true })
      onCreated()
    } catch {
      setSaving(false); setErr('Failed to create rule.'); onError()
    }
  }

  return (
    <Modal
      title="New Alert Rule"
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Create Rule'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="High CPU on core devices" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Severity</label>
            <select className={inputCls} value={severity} onChange={(e) => setSeverity(e.target.value as AlertSeverity)}>
              {(['critical', 'high', 'medium', 'low', 'info'] as AlertSeverity[]).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Cooldown (minutes)</label>
            <input className={inputCls} type="number" value={cooldown} onChange={(e) => setCooldown(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Condition (JSON)</label>
          <textarea className={`${inputCls} font-mono text-xs h-32`} value={condition} onChange={(e) => setCondition(e.target.value)} />
        </div>
      </div>
    </Modal>
  )
}

// ── Maintenance Windows (illustrative) ───────────────────────────────────────

interface Window { id: number; name: string; start: string; end: string; scope: string }

function MaintenanceTab() {
  const [windows, setWindows] = useState<Window[]>([
    { id: 1, name: 'DC-1 core upgrade', start: '2026-06-02 22:00', end: '2026-06-03 02:00', scope: 'Site: DC-1' },
  ])
  const [adding, setAdding] = useState(false)

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Create Window</button>
      </div>
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden divide-y divide-gray-100 dark:divide-gray-700">
        {windows.length === 0 ? (
          <EmptyState title="No maintenance windows" description="Schedule a window to suppress alerts and exclude downtime from SLA." icon="🛠" />
        ) : windows.map((w) => (
          <div key={w.id} className="flex items-center gap-4 px-5 py-3">
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-800 dark:text-gray-100">{w.name}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400">{w.start} → {w.end} · {w.scope}</p>
            </div>
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">Scheduled</span>
            <button onClick={() => setWindows((ws) => ws.filter((x) => x.id !== w.id))} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">Cancel</button>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Alerts are suppressed and downtime excluded from SLA during active windows.</p>

      {adding && <CreateWindowModal onClose={() => setAdding(false)} onCreate={(w) => { setWindows((ws) => [...ws, { ...w, id: Date.now() }]); setAdding(false) }} />}
    </div>
  )
}

function CreateWindowModal({ onClose, onCreate }: { onClose: () => void; onCreate: (w: Omit<Window, 'id'>) => void }) {
  const [name, setName] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [scope, setScope] = useState('')
  return (
    <Modal
      title="Create Maintenance Window"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={() => name && onCreate({ name, start, end, scope: scope || 'All devices' })} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Create</button>
        </>
      }
    >
      <div className="space-y-3">
        <div><label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Start</label><input className={inputCls} type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></div>
          <div><label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">End</label><input className={inputCls} type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
        </div>
        <div><label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Affected devices / sites</label><input className={inputCls} value={scope} onChange={(e) => setScope(e.target.value)} placeholder="Site: DC-1" /></div>
      </div>
    </Modal>
  )
}

// ── Templates (illustrative) ─────────────────────────────────────────────────

const DEFAULT_TEMPLATE = '🔴 {{severity}} — {{rule_name}}\nDevice: {{device}}\n{{message}}\nTime: {{fired_at}}'

function TemplatesTab() {
  const channels = ['Slack', 'Microsoft Teams', 'Email', 'PagerDuty']
  const [active, setActive] = useState('Slack')
  const [body, setBody] = useState(DEFAULT_TEMPLATE)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Channel type</label>
        <select className={inputCls} value={active} onChange={(e) => setActive(e.target.value)}>
          {channels.map((c) => <option key={c}>{c}</option>)}
        </select>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 mt-4">Message template</label>
        <textarea className={`${inputCls} font-mono text-xs h-48`} value={body} onChange={(e) => setBody(e.target.value)} />
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Variables: {'{{severity}} {{rule_name}} {{device}} {{message}} {{fired_at}}'}</p>
      </div>
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Preview — {active}</p>
        <div className="bg-gray-900 text-gray-100 rounded-lg p-4 text-sm whitespace-pre-wrap font-mono">
          {body
            .replace('{{severity}}', 'CRITICAL')
            .replace('{{rule_name}}', 'High CPU on core devices')
            .replace('{{device}}', 'core-rtr-01')
            .replace('{{message}}', 'CPU 96% for 5m')
            .replace('{{fired_at}}', '2026-05-29 19:42')}
        </div>
      </div>
    </div>
  )
}

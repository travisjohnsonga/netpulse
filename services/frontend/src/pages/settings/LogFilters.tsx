import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import ColorPicker from '../../components/ColorPicker'
import { SectionHeader } from '../Settings'
import {
  fetchLogFilters, createLogFilter, updateLogFilter, deleteLogFilter,
  testLogFilter, fetchDevicePlatforms,
  type LogFilter, type LogFilterAction, type PlatformOption,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const ACTION_LABELS: Record<LogFilterAction, string> = {
  suppress: 'Suppress', highlight: 'Highlight', tag: 'Tag',
}
const ACTION_BADGE: Record<LogFilterAction, string> = {
  suppress: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
  highlight: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  tag: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
}

export default function LogFilters() {
  const [filters, setFilters] = useState<LogFilter[]>([])
  const [platforms, setPlatforms] = useState<PlatformOption[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<LogFilter | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<LogFilter | null>(null)

  const load = () => {
    setLoading(true)
    Promise.all([fetchLogFilters(), fetchDevicePlatforms()])
      .then(([f, p]) => { setFilters(f); setPlatforms(p); setError(null) })
      .catch(() => setError('Failed to load log filters.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const toggleEnabled = async (f: LogFilter) => {
    // Optimistic flip; revert on error.
    setFilters((prev) => prev.map((x) => x.id === f.id ? { ...x, enabled: !x.enabled } : x))
    try {
      await updateLogFilter(f.id, { enabled: !f.enabled })
    } catch {
      setFilters((prev) => prev.map((x) => x.id === f.id ? { ...x, enabled: f.enabled } : x))
    }
  }

  return (
    <div>
      <SectionHeader
        title="Log Filters"
        description="Regex rules that suppress noise, highlight, or tag fleet log messages. Suppress filters hide matching lines from the Logs views."
        action={
          <button onClick={() => setCreating(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            + Add Filter
          </button>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : filters.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
            No log filters yet. Add a filter to suppress noise or highlight important log messages.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium">Pattern</th>
                <th className="px-5 py-3 font-medium">Action</th>
                <th className="px-5 py-3 font-medium">Platform</th>
                <th className="px-5 py-3 font-medium">Active</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {filters.map((f) => (
                <tr key={f.id} className={f.enabled ? '' : 'opacity-50'}>
                  <td className="px-5 py-3 text-gray-900 dark:text-gray-100">{f.name}</td>
                  <td className="px-5 py-3"><code className="text-xs bg-gray-100 dark:bg-gray-900 px-1.5 py-0.5 rounded text-gray-700 dark:text-gray-300">{f.pattern}</code></td>
                  <td className="px-5 py-3">
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium ${ACTION_BADGE[f.action]}`}>
                      {f.action === 'highlight' && f.color && <span className="w-2.5 h-2.5 rounded-full" style={{ background: f.color }} />}
                      {ACTION_LABELS[f.action]}{f.action === 'tag' && f.tag ? `: ${f.tag}` : ''}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">
                    {f.platforms.length === 0 ? <span className="text-gray-400">All</span> : f.platforms.join(', ')}
                  </td>
                  <td className="px-5 py-3">
                    <button onClick={() => toggleEnabled(f)} title={f.enabled ? 'Disable' : 'Enable'}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${f.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'}`}>
                      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${f.enabled ? 'translate-x-5' : 'translate-x-1'}`} />
                    </button>
                  </td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setEditing(f)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 mr-1">Edit</button>
                    <button onClick={() => setDeleting(f)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(creating || editing) && (
        <FilterModal
          filter={editing}
          platforms={platforms}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      )}
      {deleting && (
        <DeleteFilterModal
          filter={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

function FilterModal({ filter, platforms, onClose, onSaved }: {
  filter: LogFilter | null
  platforms: PlatformOption[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(filter?.name ?? '')
  const [pattern, setPattern] = useState(filter?.pattern ?? '')
  const [action, setAction] = useState<LogFilterAction>(filter?.action ?? 'suppress')
  const [color, setColor] = useState(filter?.color || '#f59e0b')
  const [tag, setTag] = useState(filter?.tag ?? '')
  const [selPlatforms, setSelPlatforms] = useState<string[]>(filter?.platforms ?? [])
  const [enabled, setEnabled] = useState(filter?.enabled ?? true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Pattern tester
  const [testMsg, setTestMsg] = useState('')
  const [testResult, setTestResult] = useState<{ matches: boolean; error: string | null } | null>(null)
  const [testing, setTesting] = useState(false)

  const togglePlatform = (val: string) =>
    setSelPlatforms((prev) => prev.includes(val) ? prev.filter((p) => p !== val) : [...prev, val])

  const runTest = async () => {
    if (!pattern.trim() || !testMsg.trim()) return
    setTesting(true)
    try {
      setTestResult(await testLogFilter(pattern, testMsg))
    } catch {
      setTestResult({ matches: false, error: 'Test request failed' })
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
        name: name.trim(), pattern: pattern.trim(), action,
        color: action === 'highlight' ? color : '',
        tag: action === 'tag' ? tag.trim() : '',
        platforms: selPlatforms, enabled,
      }
      if (filter) await updateLogFilter(filter.id, payload)
      else await createLogFilter(payload)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save filter.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={filter ? `Edit Filter: ${filter.name}` : 'Add Log Filter'}
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
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Aruba Central noise" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Pattern (regex)</label>
          <textarea className={`${inputCls} font-mono`} rows={2} value={pattern}
            onChange={(e) => { setPattern(e.target.value); setTestResult(null) }}
            placeholder="hpe-restd.*(AMM|UKWN)" />
        </div>

        {/* Pattern tester */}
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3">
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Test message</label>
          <div className="flex gap-2">
            <input className={inputCls} value={testMsg} onChange={(e) => setTestMsg(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runTest() }}
              placeholder="hpe-restd: [AMM] User admin logged in" />
            <button onClick={runTest} disabled={testing || !pattern.trim() || !testMsg.trim()}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50 text-gray-700 dark:text-gray-300 whitespace-nowrap">Test Pattern</button>
          </div>
          {testResult && (
            <div className="mt-2 text-sm">
              {testResult.error ? (
                <span className="text-amber-600 dark:text-amber-400">⚠️ Invalid regex: {testResult.error}</span>
              ) : testResult.matches ? (
                <span className="text-green-600 dark:text-green-400">✅ Pattern matches this message</span>
              ) : (
                <span className="text-gray-500 dark:text-gray-400">❌ Pattern does not match</span>
              )}
            </div>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Action</label>
          <select className={inputCls} value={action} onChange={(e) => setAction(e.target.value as LogFilterAction)}>
            <option value="suppress">Suppress (hide from log views)</option>
            <option value="highlight">Highlight</option>
            <option value="tag">Tag</option>
          </select>
        </div>
        {action === 'highlight' && (
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Highlight color</label>
            <ColorPicker value={color} onChange={setColor} />
          </div>
        )}
        {action === 'tag' && (
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Tag</label>
            <input className={inputCls} value={tag} onChange={(e) => setTag(e.target.value)} placeholder="e.g. ssh-auth" />
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Platforms <span className="text-gray-400">(none selected = all)</span></label>
          <div className="flex flex-wrap gap-1.5">
            {platforms.map((p) => {
              const on = selPlatforms.includes(p.value)
              return (
                <button key={p.value} type="button" onClick={() => togglePlatform(p.value)}
                  className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${on
                    ? 'bg-blue-600 border-blue-600 text-white'
                    : 'bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                  {p.value}
                </button>
              )
            })}
          </div>
        </div>

        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded" />
          Enabled
        </label>
      </div>
    </Modal>
  )
}

function DeleteFilterModal({ filter, onClose, onDeleted }: {
  filter: LogFilter
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const remove = async () => {
    setDeleting(true); setErr(null)
    try { await deleteLogFilter(filter.id); onDeleted() }
    catch { setErr('Failed to delete filter.'); setDeleting(false) }
  }

  return (
    <Modal
      title="Delete Filter"
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
          Delete the filter <strong>{filter.name}</strong>? This cannot be undone.
        </p>
      </div>
    </Modal>
  )
}

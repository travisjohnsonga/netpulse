import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import Modal from '../components/Modal'
import {
  fetchChecks, fetchCheckSummary, saveCheck, deleteCheck, runCheckNow,
  type ServiceCheck, type CheckStatus, type CheckType, type CheckSummary,
  type ServiceCheckPayload,
} from '../api/client'

const STATUS_BADGE: Record<CheckStatus, string> = {
  up: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  down: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  degraded: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}
const STATUS_DOT: Record<CheckStatus, string> = {
  up: 'bg-green-500', down: 'bg-red-500', degraded: 'bg-yellow-500', unknown: 'bg-gray-400',
}
// Stage 1 ships HTTP/HTTPS + TCP handlers; the rest are model-ready (no runner yet).
const STAGE1_TYPES: CheckType[] = ['https', 'http', 'tcp']

function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

export default function Checks() {
  const [checks, setChecks] = useState<ServiceCheck[]>([])
  const [summary, setSummary] = useState<CheckSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchChecks({ ordering: 'name' }), fetchCheckSummary()])
      .then(([c, s]) => { setChecks(c); setSummary(s); setError(null) })
      .catch(() => setError('Could not load service checks. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const handleRunNow = async (id: number) => {
    setBusyId(id)
    try { await runCheckNow(id); load() } finally { setBusyId(null) }
  }
  const handleDelete = async (id: number) => {
    if (!confirm('Delete this check?')) return
    setBusyId(id)
    try { await deleteCheck(id); load() } finally { setBusyId(null) }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Service Checks</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Agentless synthetic monitoring — NetPulse probes services externally.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
        >+ Add Check</button>
      </div>

      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
      )}

      {/* Summary bar */}
      {summary && (
        <div className="flex flex-wrap items-center gap-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3 text-sm">
          <span className="font-medium text-gray-700 dark:text-gray-200">{summary.total} checks</span>
          <span className="inline-flex items-center gap-1.5 text-green-600"><span className="w-2 h-2 rounded-full bg-green-500" />{summary.up} up</span>
          <span className="inline-flex items-center gap-1.5 text-red-600"><span className="w-2 h-2 rounded-full bg-red-500" />{summary.down} down</span>
          <span className="inline-flex items-center gap-1.5 text-yellow-600"><span className="w-2 h-2 rounded-full bg-yellow-500" />{summary.degraded} degraded</span>
          {summary.unknown > 0 && (
            <span className="inline-flex items-center gap-1.5 text-gray-500"><span className="w-2 h-2 rounded-full bg-gray-400" />{summary.unknown} unknown</span>
          )}
        </div>
      )}

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : checks.length === 0 ? (
          <EmptyState
            title="No service checks yet"
            description="Add an HTTP, HTTPS or TCP check to start monitoring a service externally."
            action={{ label: 'Add Check', onClick: () => setShowAdd(true) }}
            icon="✓"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Target</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Response</th>
                  <th className="px-5 py-3 font-medium">Checked</th>
                  <th className="px-5 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {checks.map((c) => (
                  <tr key={c.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">
                      {c.name}
                      {!c.is_enabled && <span className="ml-2 text-xs text-gray-400">(paused)</span>}
                    </td>
                    <td className="px-5 py-3 uppercase text-xs text-gray-500">{c.check_type}</td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-600 dark:text-gray-300">
                      {c.host}{c.effective_port ? `:${c.effective_port}` : ''}
                    </td>
                    <td className="px-5 py-3">
                      <span className="inline-flex items-center gap-1.5">
                        <span className={clsx('w-2 h-2 rounded-full', STATUS_DOT[c.current_status])} />
                        <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[c.current_status])}>
                          {c.current_status}
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-300">{fmtMs(c.last_response_ms)}</td>
                    <td className="px-5 py-3 text-gray-500 text-xs">
                      {c.last_checked ? new Date(c.last_checked).toLocaleTimeString() : 'never'}
                    </td>
                    <td className="px-5 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={() => handleRunNow(c.id)}
                        disabled={busyId === c.id}
                        className="text-blue-600 hover:text-blue-800 disabled:opacity-40 text-xs font-medium mr-3"
                      >Run now</button>
                      <button
                        onClick={() => handleDelete(c.id)}
                        disabled={busyId === c.id}
                        className="text-red-600 hover:text-red-800 disabled:opacity-40 text-xs font-medium"
                      >Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showAdd && (
        <AddCheckModal onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); load() }} />
      )}
    </div>
  )
}

function AddCheckModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<ServiceCheckPayload>({
    name: '', check_type: 'https', host: '', interval_seconds: 60, timeout_seconds: 10,
    failures_before_alert: 2,
  })
  const [path, setPath] = useState('/')
  const [warnMs, setWarnMs] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const isHttp = form.check_type === 'http' || form.check_type === 'https'

  const submit = async () => {
    setErr(null)
    if (!form.name?.trim() || !form.host?.trim()) { setErr('Name and host are required.'); return }
    setBusy(true)
    try {
      const payload: ServiceCheckPayload = { ...form }
      if (isHttp) payload.config = { path }
      if (warnMs) payload.response_time_warning_ms = Number(warnMs)
      await saveCheck(payload)
      onSaved()
    } catch {
      setErr('Could not save the check.')
    } finally {
      setBusy(false)
    }
  }

  const input = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
  const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

  return (
    <Modal
      title="Add Service Check"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={busy} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {busy ? 'Saving…' : 'Save Check'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{err}</div>}
        <div>
          <label className={label}>Name</label>
          <input className={input} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Company Website" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Type</label>
            <select className={input} value={form.check_type} onChange={(e) => setForm({ ...form, check_type: e.target.value as CheckType })}>
              {STAGE1_TYPES.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Port <span className="text-gray-400">(optional)</span></label>
            <input type="number" className={input} value={form.port ?? ''} onChange={(e) => setForm({ ...form, port: e.target.value ? Number(e.target.value) : null })} placeholder="auto" />
          </div>
        </div>
        <div>
          <label className={label}>Host</label>
          <input className={input} value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="app.example.com" />
        </div>
        {isHttp && (
          <div>
            <label className={label}>Path</label>
            <input className={input} value={path} onChange={(e) => setPath(e.target.value)} placeholder="/health" />
          </div>
        )}
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className={label}>Interval (s)</label>
            <input type="number" className={input} value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })} />
          </div>
          <div>
            <label className={label}>Timeout (s)</label>
            <input type="number" className={input} value={form.timeout_seconds} onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) })} />
          </div>
          <div>
            <label className={label}>Fails→alert</label>
            <input type="number" className={input} value={form.failures_before_alert} onChange={(e) => setForm({ ...form, failures_before_alert: Number(e.target.value) })} />
          </div>
        </div>
        <div>
          <label className={label}>Slow-response warning (ms) <span className="text-gray-400">(optional)</span></label>
          <input type="number" className={input} value={warnMs} onChange={(e) => setWarnMs(e.target.value)} placeholder="e.g. 500" />
        </div>
      </div>
    </Modal>
  )
}

import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'
import {
  fetchDiscoveryJobs, createDiscoveryJob, deleteDiscoveryJob,
  fetchDiscoveredDevices, approveDiscoveredDevice, rejectDiscoveredDevice,
  type DiscoveryJob, type DiscoveryMethod, type DiscoveredDevice,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const STATUS_BADGE: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  pending: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  cancelled: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
}

const METHOD_LABEL: Record<DiscoveryMethod, string> = {
  passive: 'Passive', topology: 'Topology Walk', scan: 'Active Scan', import: 'Import',
}

function apiError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { error?: string; detail?: string } } }
  return e?.response?.data?.error || e?.response?.data?.detail || fallback
}

export default function Discovery() {
  const [jobs, setJobs] = useState<DiscoveryJob[]>([])
  const [pending, setPending] = useState<DiscoveredDevice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchDiscoveryJobs(), fetchDiscoveredDevices('pending')])
      .then(([j, p]) => { setJobs(j); setPending(p); setError(null) })
      .catch(() => setError('Failed to load discovery data.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const removeJob = async (id: number) => {
    setBusyId(id)
    try { await deleteDiscoveryJob(id); load() }
    catch (e) { setError(apiError(e, 'Failed to delete job.')); setBusyId(null) }
  }

  const decide = async (d: DiscoveredDevice, action: 'approve' | 'reject') => {
    setBusyId(d.id); setError(null)
    try {
      if (action === 'approve') await approveDiscoveredDevice(d.id)
      else await rejectDiscoveredDevice(d.id)
      load()
    } catch (e) {
      setError(apiError(e, `Failed to ${action} device.`)); setBusyId(null)
    }
  }

  return (
    <div>
      <SectionHeader
        title="Discovery"
        description="Discovery jobs, subnet scope and OT/ICS exclusions. Discovered devices require approval before becoming active."
        action={<button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ New Job</button>}
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          {/* Jobs */}
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden mb-6">
            {jobs.length === 0 ? (
              <EmptyState title="No discovery jobs" description="Create a job to scan subnets or walk topology. Found devices land in Pending for review." action={{ label: 'New Job', onClick: () => setAdding(true) }} icon="🔍" />
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-gray-700">
                {jobs.map((j) => (
                  <div key={j.id} className={clsx('flex items-center gap-4 px-5 py-3', busyId === j.id && 'opacity-50')}>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-gray-800 dark:text-gray-100">{j.name}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {METHOD_LABEL[j.method]} · {j.devices_found} found
                        {j.pending_count > 0 && <span className="text-amber-600 dark:text-amber-400"> · {j.pending_count} pending approval</span>}
                      </p>
                    </div>
                    <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full capitalize', STATUS_BADGE[j.status])}>{j.status}</span>
                    <button onClick={() => removeJob(j.id)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">Delete</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Pending discovered devices */}
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">
            Pending approval {pending.length > 0 && <span className="text-amber-600 dark:text-amber-400">({pending.length})</span>}
          </h3>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
            {pending.length === 0 ? (
              <div className="px-5 py-8 text-center text-sm text-gray-400 dark:text-gray-500">No devices awaiting approval.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                      <th className="px-5 py-3 font-medium">IP</th>
                      <th className="px-5 py-3 font-medium">Hostname</th>
                      <th className="px-5 py-3 font-medium">Vendor / Platform</th>
                      <th className="px-5 py-3 font-medium">Confidence</th>
                      <th className="px-5 py-3 font-medium text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                    {pending.map((d) => (
                      <tr key={d.id} className={clsx('hover:bg-gray-50 dark:hover:bg-gray-700/50', busyId === d.id && 'opacity-50')}>
                        <td className="px-5 py-3 font-mono text-gray-700 dark:text-gray-300">{d.source_ip}</td>
                        <td className="px-5 py-3 text-gray-700 dark:text-gray-300">{d.discovered_hostname || '—'}</td>
                        <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{[d.discovered_vendor, d.discovered_platform].filter(Boolean).join(' / ') || '—'}</td>
                        <td className="px-5 py-3">
                          <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium',
                            d.confidence_score >= 60 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                              : d.confidence_score >= 30 ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
                                : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400')}>
                            {d.confidence_score}
                          </span>
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-2">
                            <button disabled={busyId === d.id} onClick={() => decide(d, 'approve')} className="px-2.5 py-1 text-xs bg-green-600 hover:bg-green-700 text-white rounded-md disabled:opacity-50">Approve</button>
                            <button disabled={busyId === d.id} onClick={() => decide(d, 'reject')} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 disabled:opacity-50">Reject</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {adding && <NewJobModal onClose={() => setAdding(false)} onCreated={() => { setAdding(false); load() }} />}
    </div>
  )
}

function NewJobModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [method, setMethod] = useState<DiscoveryMethod>('scan')
  const [subnets, setSubnets] = useState('')
  const [allowed, setAllowed] = useState('10.0.0.0/8')
  const [excluded, setExcluded] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const parse = (s: string) => s.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean)

  const submit = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    try {
      await createDiscoveryJob({
        name: name.trim(), method,
        subnets: parse(subnets),
        allowed_subnets: parse(allowed),
        excluded_subnets: parse(excluded),
      })
      onCreated()
    } catch (e) {
      setSaving(false); setErr(apiError(e, 'Failed to create job.'))
    }
  }

  return (
    <Modal
      title="New Discovery Job"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Creating…' : 'Create'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="DC-1 active scan" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Method</label>
          <select className={inputCls} value={method} onChange={(e) => setMethod(e.target.value as DiscoveryMethod)}>
            {(['passive', 'topology', 'scan', 'import'] as DiscoveryMethod[]).map((m) => <option key={m} value={m}>{METHOD_LABEL[m]}</option>)}
          </select>
        </div>
        {method === 'scan' && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Subnets to scan</label>
            <textarea className={`${inputCls} font-mono text-xs h-16`} value={subnets} onChange={(e) => setSubnets(e.target.value)} placeholder="10.1.0.0/24&#10;10.2.0.0/24" />
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">One CIDR per line.</p>
          </div>
        )}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Allowed subnets</label>
          <textarea className={`${inputCls} font-mono text-xs h-14`} value={allowed} onChange={(e) => setAllowed(e.target.value)} placeholder="10.0.0.0/8" />
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Discovery never probes outside these ranges.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-red-600 dark:text-red-400 mb-1">Excluded subnets (OT/ICS)</label>
          <textarea className={`${inputCls} font-mono text-xs h-14`} value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="10.99.0.0/16" />
          <p className="text-xs text-red-600 dark:text-red-400 mt-0.5">⚠ Exclude OT/ICS/SCADA networks — probing controllers can cause physical damage.</p>
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500">Discovered devices land in PENDING and require admin approval before becoming active.</p>
      </div>
    </Modal>
  )
}

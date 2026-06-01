import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'
import {
  fetchDiscoveryJobs, createDiscoveryJob, updateDiscoveryJob, runDiscoveryJob,
  restartDiscoveryJob, cancelDiscoveryJob, deleteDiscoveryJob,
  fetchDiscoveredDevices, approveDiscoveredDevice, rejectDiscoveredDevice,
  fetchCredentials, fetchDiscoveryProgress, fetchJobDiscovered,
  type DiscoveryJob, type DiscoveryMethod, type DiscoveredDevice,
  type CredentialProfileListItem, type DiscoveryProgress,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const STATUS_BADGE: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse',
  completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  pending: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  cancelled: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
}

const METHOD_LABEL: Record<DiscoveryMethod, string> = {
  passive: 'Passive', topology: 'Topology Walk', scan: 'Active Scan', import: 'Import',
}

function apiError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { error?: string; detail?: string } } }
  return e?.response?.data?.error || e?.response?.data?.detail || fallback
}

// Device platforms offered when approving an unidentified device.
const PLATFORMS = [
  'ios', 'ios_xe', 'ios_xr', 'nxos', 'asa', 'eos', 'junos',
  'fortios', 'panos', 'vyos', 'linux', 'other',
]

// Link badge shown instead of Approve/Reject when a discovered device already
// matches an inventory device.
function InventoryBadge({ deviceId }: { deviceId: number }) {
  return (
    <Link to={`/devices/${deviceId}`}
      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50 font-medium">
      Already in inventory →
    </Link>
  )
}

export default function Discovery() {
  const [jobs, setJobs] = useState<DiscoveryJob[]>([])
  const [pending, setPending] = useState<DiscoveredDevice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editJob, setEditJob] = useState<DiscoveryJob | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [approving, setApproving] = useState<DiscoveredDevice | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const flash = (msg: string) => { setNotice(msg); setTimeout(() => setNotice(null), 3000) }
  const openNew = () => { setEditJob(null); setShowModal(true) }
  const openEdit = (j: DiscoveryJob) => { setEditJob(j); setShowModal(true) }

  const startJob = async (j: DiscoveryJob) => {
    setBusyId(j.id); setError(null)
    try {
      // Pending jobs run; finished/cancelled jobs restart (both reset+execute).
      if (j.status === 'pending') await runDiscoveryJob(j.id)
      else await restartDiscoveryJob(j.id)
      flash('Discovery started'); load(true)
    } catch (e) { setError(apiError(e, 'Failed to start job.')) }
    finally { setBusyId(null) }
  }

  const cancelJob = async (j: DiscoveryJob) => {
    const msg = j.status === 'running'
      ? 'Cancel this scan? Progress will be lost.'
      : 'Cancel this job?'
    if (!window.confirm(msg)) return
    setBusyId(j.id); setError(null)
    try { await cancelDiscoveryJob(j.id); flash('Job cancelled'); load(true) }
    catch (e) { setError(apiError(e, 'Failed to cancel job.')) }
    finally { setBusyId(null) }
  }

  // silent=true refreshes data without flipping the loading spinner, so a
  // background refresh (e.g. a running job finishing) doesn't blank the list
  // and remount the expandable job rows (which would collapse them).
  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true)
    Promise.all([fetchDiscoveryJobs(), fetchDiscoveredDevices('pending')])
      .then(([j, p]) => { setJobs(j); setPending(p); setError(null) })
      .catch(() => setError('Failed to load discovery data.'))
      .finally(() => { if (!silent) setLoading(false) })
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { fetchCredentials().then(setProfiles).catch(() => {}) }, [])

  const removeJob = async (id: number) => {
    setBusyId(id)
    try { await deleteDiscoveryJob(id); load() }
    catch (e) { setError(apiError(e, 'Failed to delete job.')); setBusyId(null) }
  }

  const reject = async (d: DiscoveredDevice) => {
    setBusyId(d.id); setError(null)
    try { await rejectDiscoveredDevice(d.id); load() }
    catch (e) { setError(apiError(e, 'Failed to reject device.')); setBusyId(null) }
  }

  const confirmApprove = async (d: DiscoveredDevice, credentialProfileId: number | null, platform: string) => {
    setBusyId(d.id); setError(null)
    try {
      const res = await approveDiscoveredDevice(d.id, { credentialProfileId, platform })
      setApproving(null)
      flash(res.already_exists ? `Already in inventory — ${res.device.hostname}` : `Added ${res.device.hostname}`)
      load(true)
    } catch (e) {
      setError(apiError(e, 'Failed to approve device.'))
    } finally { setBusyId(null) }
  }

  return (
    <div>
      <SectionHeader
        title="Discovery"
        description="Discovery jobs, subnet scope and OT/ICS exclusions. Discovered devices require approval before becoming active."
        action={<button onClick={openNew} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ New Job</button>}
      />

      {notice && <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg px-4 py-3 text-sm text-green-700 dark:text-green-400 mb-4">✅ {notice}</div>}
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
              <EmptyState title="No discovery jobs" description="Create a job to scan subnets or walk topology. Found devices land in Pending for review." action={{ label: 'New Job', onClick: openNew }} icon="🔍" />
            ) : (
              <div className="divide-y divide-gray-100 dark:divide-gray-700">
                {jobs.map((j) => (
                  <JobRow key={j.id} job={j} busy={busyId === j.id}
                    onDelete={() => removeJob(j.id)}
                    onEdit={() => openEdit(j)}
                    onStart={() => startJob(j)}
                    onCancel={() => cancelJob(j)}
                    onApprove={(d) => setApproving(d)}
                    onReject={reject}
                    onChanged={() => load(true)} />
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
                            {d.already_exists && d.existing_device_id != null ? (
                              <InventoryBadge deviceId={d.existing_device_id} />
                            ) : (
                              <>
                                <button disabled={busyId === d.id} onClick={() => setApproving(d)} className="px-2.5 py-1 text-xs bg-green-600 hover:bg-green-700 text-white rounded-md disabled:opacity-50">Approve</button>
                                <button disabled={busyId === d.id} onClick={() => reject(d)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 disabled:opacity-50">Reject</button>
                              </>
                            )}
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

      {showModal && (
        <JobModal
          job={editJob ?? undefined}
          profiles={profiles}
          onClose={() => setShowModal(false)}
          onSaved={(edited) => { setShowModal(false); flash(edited ? 'Job updated' : 'Job created'); load(true) }}
        />
      )}
      {approving && (
        <ApproveModal
          device={approving}
          profiles={profiles}
          defaultProfileId={jobs.find((j) => j.id === approving.job)?.credential_profile ?? null}
          busy={busyId === approving.id}
          onClose={() => setApproving(null)}
          onConfirm={(cid, platform) => confirmApprove(approving, cid, platform)}
        />
      )}
    </div>
  )
}

function protocolsOf(d: DiscoveredDevice): string[] {
  const set = new Set<string>()
  for (const m of d.detection_methods || []) set.add(m.toUpperCase())
  for (const [k, v] of Object.entries(d.responds_to || {})) if (v) set.add(k.toUpperCase())
  return [...set]
}

function fmtSecs(s: number): string {
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

const STATUS_ICON: Record<string, string> = {
  running: '🔄', completed: '✅', failed: '❌', pending: '⏳', cancelled: '✖',
}

/** Expandable discovery-job row with live progress (polls while running). */
function JobRow({ job, busy, onDelete, onEdit, onStart, onCancel, onApprove, onReject, onChanged }: {
  job: DiscoveryJob
  busy: boolean
  onDelete: () => void
  onEdit: () => void
  onStart: () => void
  onCancel: () => void
  onApprove: (d: DiscoveredDevice) => void
  onReject: (d: DiscoveredDevice) => void
  onChanged: () => void
}) {
  const [open, setOpen] = useState(job.status === 'running')
  const [prog, setProg] = useState<DiscoveryProgress | null>(null)
  const [devices, setDevices] = useState<DiscoveredDevice[]>([])
  const [approvingAll, setApprovingAll] = useState(false)
  const notified = useRef(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false

    // Load a snapshot for display only — never touches the parent, so opening
    // an already-finished job can't trigger a reload that remounts (and thus
    // collapses) this row.
    const snapshot = async () => {
      try {
        const [p, d] = await Promise.all([fetchDiscoveryProgress(job.id), fetchJobDiscovered(job.id)])
        if (!cancelled) { setProg(p); setDevices(d) }
        return p
      } catch { return undefined }
    }

    if (job.status === 'running') {
      // Poll while running; tell the parent ONCE when it finishes (silent
      // refresh) so the status badge / pending count update.
      const tick = async () => {
        const p = await snapshot()
        if (!cancelled && p && p.status !== 'running' && !notified.current) {
          notified.current = true
          onChanged()
        }
      }
      tick()
      const t = setInterval(tick, 2000)
      return () => { cancelled = true; clearInterval(t) }
    }

    snapshot()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, job.id, job.status])

  const status = prog?.status ?? job.status
  const pct = prog?.progress_pct ?? job.progress_pct
  const message = prog?.progress_message ?? job.progress_message
  const scanned = prog?.ips_scanned ?? job.ips_scanned
  const total = prog?.progress_total ?? job.progress_total
  const found = prog?.devices_found ?? job.devices_found
  const elapsed = prog?.elapsed_seconds ?? 0
  const err = prog?.error_message ?? job.error_message
  const eta = status === 'running' && pct > 0 && pct < 100 ? Math.round(elapsed * (100 - pct) / pct) : null
  const isRunning = status === 'running'
  const isFailed = status === 'failed'

  const approveAll = async () => {
    setApprovingAll(true)
    // Skip devices already in inventory (and those needing a platform choice).
    for (const d of devices.filter((x) => x.status === 'pending' && !x.already_exists && x.discovered_platform)) {
      try { await approveDiscoveredDevice(d.id) } catch { /* surfaced via reload */ }
    }
    setApprovingAll(false)
    notified.current = false
    onChanged()
    fetchJobDiscovered(job.id).then(setDevices).catch(() => {})
  }

  const pendingCount = devices.filter(
    (d) => d.status === 'pending' && !d.already_exists && d.discovered_platform).length

  return (
    <div className={clsx(busy && 'opacity-50')}>
      <div className="flex items-center gap-3 px-5 py-3">
        <button onClick={() => setOpen((o) => !o)} className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 w-4 shrink-0" title={open ? 'Collapse' : 'Expand'}>
          {open ? '▼' : '▶'}
        </button>
        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setOpen((o) => !o)}>
          <p className="font-medium text-gray-800 dark:text-gray-100">{job.name}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {METHOD_LABEL[job.method]} · {found} found
            {job.pending_count > 0 && <span className="text-amber-600 dark:text-amber-400"> · {job.pending_count} pending approval</span>}
          </p>
        </div>
        {isRunning && elapsed > 0 && <span className="text-xs text-gray-400 dark:text-gray-500">{fmtSecs(elapsed)}</span>}
        <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full capitalize', STATUS_BADGE[status])}>{STATUS_ICON[status]} {status}</span>
        {(() => {
          const canStart = job.method === 'scan' || job.method === 'topology'
          const startLabel = status === 'completed' ? '▶ Run Again' : status === 'failed' ? '▶ Retry' : '▶ Run'
          const btn = 'px-2.5 py-1 text-xs border rounded-md disabled:opacity-50'
          const plain = 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300'
          return (
            <>
              {/* Run / Run Again / Retry — hidden while running */}
              {canStart && !isRunning && (
                <button onClick={onStart} className={clsx(btn, plain)}>{startLabel}</button>
              )}
              {/* Edit — hidden while running (can't edit a running job) */}
              {!isRunning && (
                <button onClick={onEdit} className={clsx(btn, plain)} title="Edit this job">✏️ Edit</button>
              )}
              {/* Cancel — only for pending / running */}
              {(status === 'pending' || status === 'running') && (
                <button onClick={onCancel}
                  className={clsx(btn, 'border-orange-300 dark:border-orange-700 text-orange-700 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/30')}>
                  ✖ Cancel
                </button>
              )}
              <button onClick={onDelete} className={clsx(btn, plain)}>🗑 Delete</button>
            </>
          )
        })()}
      </div>

      {open && (
        <div className="px-5 pb-4 pt-1 bg-gray-50 dark:bg-gray-900/40 border-t border-gray-100 dark:border-gray-700">
          {/* Progress bar */}
          <div className="mt-2 mb-2">
            <div className="h-3 w-full rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div className={clsx('h-full rounded-full transition-all', isFailed ? 'bg-red-500' : isRunning ? 'bg-green-500 animate-pulse' : 'bg-green-500')}
                style={{ width: `${pct}%` }} />
            </div>
            <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mt-1">
              <span>{message || (isRunning ? 'Starting…' : '')}</span>
              <span>{pct}%</span>
            </div>
          </div>

          {/* Stats line */}
          <p className="text-xs text-gray-600 dark:text-gray-300">
            {isFailed ? (
              <>Scanned {scanned}{total ? `/${total}` : ''} IPs before failure</>
            ) : isRunning ? (
              <>⏱ Elapsed: {fmtSecs(elapsed)}{eta != null && <> · ETA: ~{fmtSecs(eta)}</>}</>
            ) : (
              <>Scanned: {scanned || total} IPs · Found: {found} devices{elapsed > 0 && <> · Time: {fmtSecs(elapsed)}</>}</>
            )}
          </p>

          {isFailed && err && (
            <div className="mt-2 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-md px-3 py-2 text-xs text-red-700 dark:text-red-400">
              ❌ {err}
            </div>
          )}

          {/* Discovered devices */}
          {devices.length > 0 && (
            <div className="mt-3">
              <div className="flex items-center justify-between mb-1">
                <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  {isRunning ? `Devices found so far: ${found}` : 'Discovered devices'}
                </p>
                {!isRunning && pendingCount > 0 && (
                  <button onClick={approveAll} disabled={approvingAll}
                    className="px-2.5 py-1 text-xs bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-md">
                    {approvingAll ? 'Approving…' : `Approve All (${pendingCount})`}
                  </button>
                )}
              </div>
              <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 text-left">
                      <th className="px-3 py-1.5 font-medium">IP</th>
                      <th className="px-3 py-1.5 font-medium">Hostname</th>
                      <th className="px-3 py-1.5 font-medium">Platform</th>
                      <th className="px-3 py-1.5 font-medium">Protocols</th>
                      <th className="px-3 py-1.5 font-medium text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                    {devices.map((d) => (
                      <tr key={d.id} className="text-gray-700 dark:text-gray-300">
                        <td className="px-3 py-1.5 font-mono">{d.source_ip}</td>
                        <td className="px-3 py-1.5">{d.discovered_hostname || '—'}</td>
                        <td className="px-3 py-1.5">{d.discovered_platform || 'unknown'}</td>
                        <td className="px-3 py-1.5">
                          <span className="inline-flex gap-1">
                            {protocolsOf(d).map((p) => <span key={p} className="px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">{p}</span>)}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          {d.already_exists && d.existing_device_id != null ? (
                            <InventoryBadge deviceId={d.existing_device_id} />
                          ) : d.status === 'pending' ? (
                            <span className="inline-flex gap-1.5 justify-end">
                              <button onClick={() => onApprove(d)} className="px-2 py-0.5 bg-green-600 hover:bg-green-700 text-white rounded">Approve</button>
                              <button onClick={() => onReject(d)} className="px-2 py-0.5 border border-gray-300 dark:border-gray-600 rounded dark:text-gray-300">Reject</button>
                            </span>
                          ) : <span className="capitalize text-gray-400">{d.status}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ApproveModal({ device, profiles, defaultProfileId, busy, onClose, onConfirm }: {
  device: DiscoveredDevice
  profiles: CredentialProfileListItem[]
  defaultProfileId: number | null
  busy: boolean
  onClose: () => void
  onConfirm: (credentialProfileId: number | null, platform: string) => void
}) {
  const [credId, setCredId] = useState<number | null>(defaultProfileId)
  // Discovery couldn't identify the platform → require the operator to pick one.
  const platformUnknown = !device.discovered_platform
  const [platform, setPlatform] = useState(device.discovered_platform || '')

  return (
    <Modal
      title={`Approve ${device.discovered_hostname || device.source_ip}`}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={() => onConfirm(credId, platform)} disabled={busy || (platformUnknown && !platform)}
            className="flex-1 py-2.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Approving…' : 'Approve & Add'}</button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-300">
          Adding <span className="font-mono">{device.source_ip}</span> to inventory as an active device.
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Platform {platformUnknown && <span className="text-red-500">*</span>}
          </label>
          <select className={inputCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">— Select platform —</option>
            {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          {platformUnknown && (
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">Discovery couldn't identify the platform — please select one.</p>
          )}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Which credentials does this device use?</label>
          <select className={inputCls} value={credId ?? ''} onChange={(e) => setCredId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— No profile (set later) —</option>
            {profiles.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.enabled_protocols.join(', ') || 'none'})</option>)}
          </select>
          <a href="/settings/credentials" target="_blank" rel="noreferrer" className="inline-block text-xs text-blue-600 hover:text-blue-800 mt-1">+ Create new credentials (opens Settings → Credentials)</a>
        </div>
      </div>
    </Modal>
  )
}

function JobModal({ job, profiles, onClose, onSaved }: { job?: DiscoveryJob; profiles: CredentialProfileListItem[]; onClose: () => void; onSaved: (edited: boolean) => void }) {
  const editing = !!job
  const [name, setName] = useState(job?.name ?? '')
  const [method, setMethod] = useState<DiscoveryMethod>(job?.method ?? 'scan')
  const [subnets, setSubnets] = useState(job?.subnets.join('\n') ?? '')
  const [allowed, setAllowed] = useState(job ? job.allowed_subnets.join('\n') : '10.0.0.0/8')
  const [excluded, setExcluded] = useState(job?.excluded_subnets.join('\n') ?? '')
  const [credId, setCredId] = useState<number | null>(job?.credential_profile ?? null)
  const [maxDevices, setMaxDevices] = useState(job?.max_devices ?? 1000)
  const [ratePps, setRatePps] = useState(job?.rate_limit_pps ?? 10)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const parse = (s: string) => s.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean)

  // Credentials are needed to actually connect: SNMP (community/v3) for scan
  // fingerprinting, SSH for LLDP/topology walks.
  const needsCreds = method === 'scan' || method === 'topology'

  const submit = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    const payload = {
      name: name.trim(), method,
      subnets: parse(subnets),
      allowed_subnets: parse(allowed),
      excluded_subnets: parse(excluded),
      credential_profile: needsCreds ? credId : null,
      max_devices: maxDevices,
      rate_limit_pps: ratePps,
    }
    try {
      if (editing) await updateDiscoveryJob(job.id, payload)
      else await createDiscoveryJob(payload)
      onSaved(editing)
    } catch (e) {
      setSaving(false); setErr(apiError(e, `Failed to ${editing ? 'update' : 'create'} job.`))
    }
  }

  return (
    <Modal
      title={editing ? 'Edit Discovery Job' : 'New Discovery Job'}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : editing ? 'Save Changes' : 'Create'}</button>
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
        {needsCreds && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Credentials to use for discovered devices</label>
            <select className={inputCls} value={credId ?? ''} onChange={(e) => setCredId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">— No profile (limited discovery) —</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.enabled_protocols.join(', ') || 'none'})</option>)}
            </select>
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              SNMP community/SNMPv3 is used to fingerprint scanned IPs; SSH is used for LLDP/topology. Without a profile, discovery falls back to the <code>public</code> community only. Secrets stay in OpenBao and are assigned to devices on approval.
            </p>
            <a href="/settings/credentials" target="_blank" rel="noreferrer" className="inline-block text-xs text-blue-600 hover:text-blue-800 mt-1">+ Create a credential profile (opens Settings → Credentials)</a>
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
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max devices</label>
            <input type="number" min={1} className={inputCls} value={maxDevices} onChange={(e) => setMaxDevices(Number(e.target.value))} />
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Circuit breaker.</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Rate limit (pps)</label>
            <input type="number" min={1} className={inputCls} value={ratePps} onChange={(e) => setRatePps(Number(e.target.value))} />
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Probes per second.</p>
          </div>
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500">Discovered devices land in PENDING and require admin approval before becoming active.</p>
      </div>
    </Modal>
  )
}

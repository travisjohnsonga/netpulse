import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'
import {
  fetchDiscoveryJobs, createDiscoveryJob, updateDiscoveryJob, runDiscoveryJob,
  restartDiscoveryJob, cancelDiscoveryJob, deleteDiscoveryJob,
  approveDiscoveredDevice, rejectDiscoveredDevice,
  fetchCredentials, fetchDiscoveryProgress, fetchJobDiscovered, fetchSites,
  type DiscoveryJob, type DiscoveryMethod, type DiscoveredDevice,
  type CredentialProfileListItem, type DiscoveryProgress, type Site,
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
  ping_snmp: 'Ping + SNMP', topology: 'Topology Walk', passive: 'Passive',
  scan: 'Active Scan', ping: 'Ping Only', import: 'Import',
}

// Order shown in the New Job modal — safest (production-friendly) first.
const METHOD_ORDER: DiscoveryMethod[] = ['ping_snmp', 'topology', 'passive', 'scan', 'ping', 'import']

// Per-method guidance shown below the Method dropdown.
const METHOD_DESC: Record<DiscoveryMethod, string> = {
  ping_snmp: '✅ Safe for production. ICMP ping sweep + SNMP fingerprinting (and a non-intrusive SSH banner read) only. No port scanning — won’t trigger IDS/firewall rules.',
  topology: 'Walks LLDP/CDP neighbors from a seed device. Requires SNMP credentials on the seed.',
  passive: 'Listen-only mode. No active probing — devices appear as their traffic is seen by the ingest layer.',
  scan: '⚠️ Uses nmap port scanning. May trigger IDS/firewall alerts. Recommended for lab/test environments only.',
  ping: 'ICMP ping sweep only. No fingerprinting — platform will be unknown and require manual selection on approval.',
  import: 'Import devices from a NetBox/CSV source rather than probing the network.',
}

function apiError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { error?: string; detail?: string } } }
  return e?.response?.data?.error || e?.response?.data?.detail || fallback
}

// Device platforms offered when approving an unidentified device.
const PLATFORMS = [
  'ios', 'ios_xe', 'ios_xr', 'nxos', 'asa', 'eos', 'junos',
  'fortios', 'panos', 'sonicwall', 'aos_cx', 'aruba', 'vyos', 'linux', 'other',
]

// Default platform for an unambiguous vendor (mirrors the backend
// default_platform_for_vendor). '' for multi-platform vendors like cisco, which
// need an explicit choice. Used to pre-fill the bulk-approve platform selector.
const VENDOR_DEFAULT_PLATFORM: Record<string, string> = {
  fortinet: 'fortios', paloalto: 'panos', arista: 'eos',
  juniper: 'junos', mikrotik: 'routeros',
}
function vendorDefaultPlatform(vendor: string | undefined): string {
  return VENDOR_DEFAULT_PLATFORM[(vendor || '').toLowerCase()] || ''
}

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

// A discovered device is bulk-approvable when it's still pending, not already
// in inventory, and we can resolve a platform for it: either discovery
// identified one, or the vendor is known (its default platform is used, or the
// operator picks one in the bulk platform prompt for multi-platform vendors).
function isApprovable(d: DiscoveredDevice): boolean {
  return d.status === 'pending' && !d.already_exists
    && (!!d.discovered_platform || !!d.discovered_vendor)
}

interface BulkSummary { ok: number; skipped: number; failed: number }

/**
 * Checkbox selection + bulk-approve for a discovered-devices table. Selection
 * is preserved across list refreshes (running-job polling) and pruned to the
 * still-eligible device ids.
 */
function useBulkApprove(devices: DiscoveredDevice[], onComplete: (s: BulkSummary) => void) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [approving, setApproving] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  // Devices in the current selection whose platform must be chosen before the
  // bulk approve can run (unknown platform), plus the per-device choices.
  const [platformPrompt, setPlatformPrompt] = useState<DiscoveredDevice[] | null>(null)
  const [platformChoices, setPlatformChoices] = useState<Record<number, string>>({})
  const headerRef = useRef<HTMLInputElement>(null)

  const eligibleIds = devices.filter(isApprovable).map((d) => d.id)

  // Keep selection across polls but drop ids that are no longer eligible.
  useEffect(() => {
    setSelected((prev) => {
      const valid = [...prev].filter((id) => eligibleIds.includes(id))
      return valid.length === prev.size ? prev : new Set(valid)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices])

  const allChecked = eligibleIds.length > 0 && eligibleIds.every((id) => selected.has(id))
  const someChecked = selected.size > 0 && !allChecked
  useEffect(() => {
    if (headerRef.current) headerRef.current.indeterminate = someChecked
  }, [someChecked])

  const toggle = (id: number) =>
    setSelected((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleAll = () =>
    setSelected(allChecked ? new Set() : new Set(eligibleIds))
  const clear = () => setSelected(new Set())

  // Run the approvals. Each device uses its discovered platform, else the
  // operator's choice from the prompt, else (for vendor-default cases) the
  // vendor default — so the approve call always carries a resolved platform.
  const runApproval = async (chosen: Record<number, string>) => {
    const ids = [...selected]
    if (!ids.length) return
    const byId = new Map(devices.map((d) => [d.id, d]))
    setApproving(true)
    const summary: BulkSummary = { ok: 0, skipped: 0, failed: 0 }
    for (let i = 0; i < ids.length; i++) {
      setProgress(`Approving ${i + 1}/${ids.length}...`)
      const d = byId.get(ids[i])
      const platform = d?.discovered_platform || chosen[ids[i]] || vendorDefaultPlatform(d?.discovered_vendor)
      try {
        const res = await approveDiscoveredDevice(ids[i], platform ? { platform } : {})
        if (res.already_exists) summary.skipped++; else summary.ok++
      } catch { summary.failed++ }
    }
    setApproving(false); setProgress(null); setSelected(new Set())
    setPlatformPrompt(null); setPlatformChoices({})
    onComplete(summary)
  }

  // Entry point for the "Approve Selected" button. If any selected device has
  // no discovered platform, prompt for one (pre-filled with the vendor default);
  // otherwise approve straight away.
  const approveSelected = () => {
    const byId = new Map(devices.map((d) => [d.id, d]))
    const sel = [...selected].map((id) => byId.get(id)).filter(Boolean) as DiscoveredDevice[]
    const needPlatform = sel.filter((d) => !d.discovered_platform)
    if (needPlatform.length) {
      const init: Record<number, string> = {}
      for (const d of needPlatform) init[d.id] = vendorDefaultPlatform(d.discovered_vendor)
      setPlatformChoices(init)
      setPlatformPrompt(needPlatform)
    } else {
      runApproval({})
    }
  }

  const setPlatformChoice = (id: number, value: string) =>
    setPlatformChoices((p) => ({ ...p, [id]: value }))
  const cancelPrompt = () => { setPlatformPrompt(null); setPlatformChoices({}) }

  return {
    selected, headerRef, allChecked, eligibleCount: eligibleIds.length,
    approving, progress, toggle, toggleAll, clear, approveSelected,
    platformPrompt, platformChoices, setPlatformChoice, cancelPrompt,
    confirmPrompt: () => runApproval(platformChoices),
  }
}

function bulkResultMessage(s: BulkSummary): string {
  const parts = [`${s.ok} device${s.ok === 1 ? '' : 's'} approved`]
  if (s.skipped) parts.push(`${s.skipped} skipped (already exists)`)
  if (s.failed) parts.push(`${s.failed} failed`)
  return parts.join(', ')
}

// Disabled checkbox (with reason tooltip) for non-approvable rows.
function RowCheckbox({ d, checked, onToggle }: {
  d: DiscoveredDevice; checked: boolean; onToggle: () => void
}) {
  if (!isApprovable(d)) {
    return (
      <input type="checkbox" disabled className="opacity-40 cursor-not-allowed"
        title={d.already_exists ? 'Already in inventory'
          : 'Unknown vendor — approve individually and pick a platform'} />
    )
  }
  return <input type="checkbox" checked={checked} onChange={onToggle} className="cursor-pointer" />
}

// Bulk-approve platform picker: shown when the selection includes devices whose
// platform discovery couldn't identify. Each is pre-filled with the vendor
// default (e.g. fortinet → fortios); multi-platform vendors start blank.
function BulkPlatformModal({ devices, choices, busy, onChoose, onCancel, onConfirm }: {
  devices: DiscoveredDevice[]
  choices: Record<number, string>
  busy: boolean
  onChoose: (id: number, value: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const allChosen = devices.every((d) => !!choices[d.id])
  return (
    <Modal
      title="Select platform"
      onClose={onCancel}
      footer={
        <>
          <button onClick={onCancel} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={onConfirm} disabled={busy || !allChosen}
            className="flex-1 py-2.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Approving…' : 'Approve All'}</button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-300">
          {devices.length} device{devices.length === 1 ? '' : 's'} {devices.length === 1 ? 'has' : 'have'} an
          unknown platform. Select one for each before approving:
        </p>
        <div className="space-y-2">
          {devices.map((d) => (
            <div key={d.id} className="flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate text-sm text-gray-700 dark:text-gray-300">
                <span className="font-mono">{d.discovered_hostname || d.source_ip}</span>
                {d.discovered_vendor && <span className="text-gray-400"> · {d.discovered_vendor}</span>}
              </span>
              <select className={clsx(inputCls, 'w-40')} value={choices[d.id] || ''}
                onChange={(e) => onChoose(d.id, e.target.value)}>
                <option value="">— Select —</option>
                {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  )
}

function BulkBar({ count, approving, progress, onApprove, onClear }: {
  count: number; approving: boolean; progress: string | null
  onApprove: () => void; onClear: () => void
}) {
  if (count === 0) return null
  return (
    <div className="flex items-center gap-3 px-3 py-2 mb-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 text-sm">
      <span className="font-medium text-blue-700 dark:text-blue-300">☑ {count} selected</span>
      {progress && <span className="text-xs text-gray-500 dark:text-gray-400">{progress}</span>}
      <div className="ml-auto flex items-center gap-2">
        <button onClick={onApprove} disabled={approving}
          className="px-2.5 py-1 text-xs bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-md font-medium">
          {approving ? 'Approving…' : '✅ Approve Selected'}
        </button>
        <button onClick={onClear} title="Clear selection"
          className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">✖</button>
      </div>
    </div>
  )
}

export default function Discovery() {
  const [jobs, setJobs] = useState<DiscoveryJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editJob, setEditJob] = useState<DiscoveryJob | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [sites, setSites] = useState<Site[]>([])
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
    fetchDiscoveryJobs()
      .then((j) => { setJobs(j); setError(null) })
      .catch(() => setError('Failed to load discovery data.'))
      .finally(() => { if (!silent) setLoading(false) })
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { fetchCredentials().then(setProfiles).catch(() => {}) }, [])
  useEffect(() => { fetchSites().then(setSites).catch(() => {}) }, [])

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
                    onNotify={flash}
                    onChanged={() => load(true)} />
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {showModal && (
        <JobModal
          job={editJob ?? undefined}
          profiles={profiles}
          sites={sites}
          onClose={() => setShowModal(false)}
          onSaved={(edited) => { setShowModal(false); flash(edited ? 'Job updated' : 'Job created'); load(true) }}
        />
      )}
      {approving && (
        <ApproveModal
          device={approving}
          profiles={profiles}
          defaultProfileId={jobs.find((j) => j.id === approving.job)?.credential_profile ?? null}
          siteName={jobs.find((j) => j.id === approving.job)?.site_name ?? null}
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
function JobRow({ job, busy, onDelete, onEdit, onStart, onCancel, onApprove, onReject, onNotify, onChanged }: {
  job: DiscoveryJob
  busy: boolean
  onDelete: () => void
  onEdit: () => void
  onStart: () => void
  onCancel: () => void
  onApprove: (d: DiscoveredDevice) => void
  onReject: (d: DiscoveredDevice) => void
  onNotify: (msg: string) => void
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

  const bulk = useBulkApprove(devices, (s) => {
    onNotify(bulkResultMessage(s))
    notified.current = false
    onChanged()
    fetchJobDiscovered(job.id).then(setDevices).catch(() => {})
  })

  return (
    <div className={clsx(busy && 'opacity-50')}>
      <div className="flex items-center gap-3 px-5 py-3">
        <button onClick={() => setOpen((o) => !o)} className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 w-4 shrink-0" title={open ? 'Collapse' : 'Expand'}>
          {open ? '▼' : '▶'}
        </button>
        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setOpen((o) => !o)}>
          <p className="font-medium text-gray-800 dark:text-gray-100">{job.name}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {METHOD_LABEL[job.method]} · {found} found · Site: {job.site_name || 'Unassigned'}
            {job.pending_count > 0 && <span className="text-amber-600 dark:text-amber-400"> · {job.pending_count} pending approval</span>}
          </p>
        </div>
        {isRunning && elapsed > 0 && <span className="text-xs text-gray-400 dark:text-gray-500">{fmtSecs(elapsed)}</span>}
        <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full capitalize', STATUS_BADGE[status])}>{STATUS_ICON[status]} {status}</span>
        {(() => {
          const canStart = job.method === 'scan' || job.method === 'topology' || job.method === 'ping_snmp' || job.method === 'ping'
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
              <BulkBar count={bulk.selected.size} approving={bulk.approving} progress={bulk.progress}
                onApprove={bulk.approveSelected} onClear={bulk.clear} />
              <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 text-left">
                      <th className="px-3 py-1.5 font-medium w-6">
                        <input ref={bulk.headerRef} type="checkbox" checked={bulk.allChecked}
                          disabled={bulk.eligibleCount === 0} onChange={bulk.toggleAll}
                          className="cursor-pointer disabled:opacity-40" title="Select all approvable" />
                      </th>
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
                        <td className="px-3 py-1.5">
                          <RowCheckbox d={d} checked={bulk.selected.has(d.id)} onToggle={() => bulk.toggle(d.id)} />
                        </td>
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
      {bulk.platformPrompt && (
        <BulkPlatformModal
          devices={bulk.platformPrompt}
          choices={bulk.platformChoices}
          busy={bulk.approving}
          onChoose={bulk.setPlatformChoice}
          onCancel={bulk.cancelPrompt}
          onConfirm={bulk.confirmPrompt}
        />
      )}
    </div>
  )
}

function ApproveModal({ device, profiles, defaultProfileId, siteName, busy, onClose, onConfirm }: {
  device: DiscoveredDevice
  profiles: CredentialProfileListItem[]
  defaultProfileId: number | null
  siteName: string | null
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
        <p className="text-sm text-gray-600 dark:text-gray-300">
          Will be assigned to: <span className="font-medium">{siteName || 'Unassigned'}</span>
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Hostname rules will auto-assign role and site on approval (where unset).
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

function JobModal({ job, profiles, sites, onClose, onSaved }: { job?: DiscoveryJob; profiles: CredentialProfileListItem[]; sites: Site[]; onClose: () => void; onSaved: (edited: boolean) => void }) {
  const editing = !!job
  const [name, setName] = useState(job?.name ?? '')
  // Default to the production-safe Ping + SNMP (Active Scan's nmap probing
  // tripped a firewall block in the wco2 lab — see CLAUDE.md).
  const [method, setMethod] = useState<DiscoveryMethod>(job?.method ?? 'ping_snmp')
  const [subnets, setSubnets] = useState(job?.subnets.join('\n') ?? '')
  const [allowed, setAllowed] = useState(job ? job.allowed_subnets.join('\n') : '10.0.0.0/8')
  const [excluded, setExcluded] = useState(job?.excluded_subnets.join('\n') ?? '')
  const [credId, setCredId] = useState<number | null>(job?.credential_profile ?? null)
  const [siteId, setSiteId] = useState<number | null>(job?.site ?? null)
  const [maxDevices, setMaxDevices] = useState(job?.max_devices ?? 1000)
  const [ratePps, setRatePps] = useState(job?.rate_limit_pps ?? 10)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const parse = (s: string) => s.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean)

  // "Allowed" still at its empty/default catch-all → safe to suggest narrowing.
  const allowedIsDefault = allowed.trim() === '' || allowed.trim() === '10.0.0.0/8'

  // Credentials are needed to actually connect: SNMP (community/v3) for
  // scan/ping+SNMP fingerprinting, SSH for LLDP/topology walks. (Ping Only does
  // no fingerprinting, so it needs none.)
  const needsCreds = method === 'scan' || method === 'topology' || method === 'ping_snmp'

  // Subnet-based methods sweep ranges; topology seeds from a device, passive/
  // import don't probe ranges at all.
  const needsSubnets = method === 'scan' || method === 'ping_snmp' || method === 'ping'

  const submit = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    const payload = {
      name: name.trim(), method,
      subnets: parse(subnets),
      allowed_subnets: parse(allowed),
      excluded_subnets: parse(excluded),
      credential_profile: needsCreds ? credId : null,
      site: siteId,
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
            {METHOD_ORDER.map((m) => <option key={m} value={m}>{METHOD_LABEL[m]}</option>)}
          </select>
          <p className={clsx('text-xs mt-1', method === 'scan' ? 'text-amber-600 dark:text-amber-400' : 'text-gray-500 dark:text-gray-400')}>
            {METHOD_DESC[method]}
          </p>
        </div>
        {needsSubnets && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Subnets to scan</label>
            <textarea className={`${inputCls} font-mono text-xs h-16`} value={subnets} onChange={(e) => setSubnets(e.target.value)} placeholder="10.1.0.0/24&#10;10.2.0.0/24" />
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">One CIDR per line.</p>
            {parse(subnets).length > 0 && allowedIsDefault && (
              <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                Tip: Click "↑ Copy from subnets to scan" below to restrict discovery to only your target subnets.
              </p>
            )}
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
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Assign to Site</label>
          <select className={inputCls} value={siteId ?? ''} onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— No site (unassigned) —</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Discovered devices will be automatically assigned to this site on approval.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Allowed subnets</label>
          <textarea className={`${inputCls} font-mono text-xs h-14`} value={allowed} onChange={(e) => setAllowed(e.target.value)} placeholder="10.0.0.0/8" />
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Discovery never probes outside these ranges.</p>
          {needsSubnets && parse(subnets).length > 0 && (
            <button type="button" onClick={() => setAllowed(subnets)}
              className="text-xs text-blue-500 hover:text-blue-400 cursor-pointer mt-1">
              ↑ Copy from subnets to scan
            </button>
          )}
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

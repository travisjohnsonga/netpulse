import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTabParam } from '../lib/useTabParam'
import clsx from 'clsx'
import { api, fetchDevice, fetchCredential, deleteDevice, discoverInterfaces, reachabilityOf, fetchCollectors, setDeviceCollector, type Collector, type DeviceDetail as Device } from '../api/client'
import { sshUrl, sshTooltip } from '../lib/ssh'
import { useWebSocket } from '../hooks/useWebSocket'
import Overview from './device/Overview'
import Telemetry, { TelemetryConfigPanel, Environment } from './device/Telemetry'
import Wireless from './device/Wireless'
import Logs from './device/Logs'
import Flows from './device/Flows'
import ArpMac from './device/ArpMac'
import Configuration from './device/Configuration'
import Compliance from './device/Compliance'
import ServiceChecks from './device/ServiceChecks'
import CVE from './device/CVE'
import Lifecycle from './device/Lifecycle'
import Modal from '../components/Modal'
import DeviceEditModal from '../components/DeviceEditModal'
import DeviceCredentialsPanel from '../components/DeviceCredentialsPanel'
import ManualLinkModal from '../components/ManualLinkModal'
import RoleBubble from '../components/RoleBubble'
import VendorLogo from '../components/VendorLogo'
import { CollectionMethodBadges } from '../components/CollectionMethodBadges'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'telemetry', label: 'Telemetry' },
  { id: 'wireless', label: 'Wireless' },
  { id: 'environment', label: 'Environment' },
  { id: 'logs', label: 'Logs' },
  { id: 'flows', label: 'Flows' },
  { id: 'arpmac', label: 'ARP / MAC' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'compliance', label: 'Compliance' },
  { id: 'checks', label: 'Service Checks' },
  { id: 'cve', label: 'CVE' },
  { id: 'lifecycle', label: 'Lifecycle' },
] as const

// string[] (not the literal union) so setTab stays compatible with child props
// typed (t: string) => void (e.g. Overview's onTab).
const TAB_IDS: string[] = TABS.map((t) => t.id)

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-gray-100 text-gray-600',
  maintenance: 'bg-yellow-100 text-yellow-700',
  decommissioned: 'bg-red-100 text-red-700',
}

export default function DeviceDetail() {
  const { id } = useParams<{ id: string }>()
  const deviceId = Number(id)
  const navigate = useNavigate()
  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Active tab lives in the URL (?tab=…) so a refresh restores it and the URL is
  // shareable; clicking a tab now writes the URL (was state-only → lost on refresh).
  const [tab, setTab] = useTabParam(TAB_IDS, 'overview')

  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [managingCreds, setManagingCreds] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)
  const [sshCred, setSshCred] = useState<{ username: string | null; port: number | null }>({ username: null, port: null })
  const [telemetryConfig, setTelemetryConfig] = useState(false)
  const [addingManualLink, setAddingManualLink] = useState(false)
  // Bumped when the Telemetry Configuration slide-over closes so the Telemetry
  // tab refetches interfaces/metrics/collection-status (shows the new selection).
  const [telemetryRefresh, setTelemetryRefresh] = useState(0)
  const [changingCollector, setChangingCollector] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    fetchDevice(deviceId)
      .then((d) => { setDevice(d); setError(null) })
      .catch((err) => {
        // A missing device (deleted, or stale link) bounces back to the list
        // with an explanatory toast instead of a dead-end error page.
        if (err?.response?.status === 404) {
          navigate('/devices', { replace: true, state: { toast: 'Device not found — it may have been deleted' } })
          return
        }
        setError('Could not load the device. The API may be unavailable.')
      })
      .finally(() => setLoading(false))
  }, [deviceId, navigate])

  useEffect(() => { load() }, [load])

  // Live reachability: reload this device when the monitor pushes a change for it.
  const { lastMessage } = useWebSocket('/ws/devices/')
  useEffect(() => {
    const m = lastMessage as { type?: string; device_id?: number } | null
    if (m && m.type === 'device_status' && m.device_id === deviceId) load()
  }, [lastMessage, deviceId, load])

  // Pull the SSH username/port from the device's credential profile so the SSH
  // link can include them (falls back to a bare ssh://<host> otherwise).
  useEffect(() => {
    if (!device?.credential_profile) { setSshCred({ username: null, port: null }); return }
    fetchCredential(device.credential_profile)
      .then((p) => setSshCred({ username: p.ssh_enabled ? p.ssh_username : null, port: p.ssh_port }))
      .catch(() => setSshCred({ username: null, port: null }))
  }, [device?.credential_profile])

  const flash = (ok: boolean, msg: string) => { setToast({ ok, msg }); setTimeout(() => setToast(null), 4000) }

  const collectNow = async () => {
    setMenuOpen(false); setBusy('collect')
    try { await api.post(`/configbackup/configs/collect/${deviceId}/`); flash(true, 'Config collection triggered') }
    catch { flash(false, 'Config collection failed') } finally { setBusy(null) }
  }

  const runDiscovery = async () => {
    setMenuOpen(false); setBusy('discover')
    try { const r = await discoverInterfaces(deviceId); flash(true, `Discovered ${r.count} interface${r.count !== 1 ? 's' : ''}`) }
    catch { flash(false, 'Discovery failed') } finally { setBusy(null) }
  }

  const confirmDelete = async () => {
    try { await deleteDevice(deviceId); navigate('/devices') }
    catch { setDeleting(false); flash(false, 'Failed to delete device') }
  }

  if (loading) {
    return <div className="flex items-center justify-center py-24"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }
  if (error || !device) {
    return (
      <div className="space-y-4">
        <Link to="/devices" className="text-sm text-blue-600 hover:text-blue-800">&larr; Back to devices</Link>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error ?? 'Device not found.'}</div>
      </div>
    )
  }

  const menuItem = 'w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700'

  return (
    <div className="space-y-4">
      {/* Sticky header + tab bar — stay visible while the tab content below
          scrolls. main (in Layout) is the scroll container; the sidebar and
          this bar never scroll off. bg matches main so content scrolls under. */}
      <div className="sticky top-0 z-20 -mx-4 lg:-mx-6 px-4 lg:px-6 -mt-4 lg:-mt-6 pt-4 lg:pt-6 bg-gray-50 dark:bg-gray-950 space-y-3">
      {/* Breadcrumb + header */}
      <div>
        <Link to="/devices" className="text-sm text-blue-600 hover:text-blue-800">&larr; Devices</Link>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mt-2">
          <div className="flex items-center gap-3">
            <VendorLogo platform={device.platform} vendor={device.vendor} size={28} />
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100" title={device.hostname}>{device.display_hostname || device.hostname}</h1>
            <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[device.status] ?? 'bg-gray-100 text-gray-600')}>{device.status}</span>
            <RoleBubble role={device.role} />
            <ReachabilityIndicator device={device} />
          </div>
          <div className="flex items-center gap-3">
            <CollectionMethodBadges deviceId={device.id} />
            <span className="text-sm text-gray-500 font-mono">{device.management_ip || device.ip_address}</span>
            <a
              href={sshUrl(device, sshCred.username, sshCred.port)}
              target="_blank" rel="noopener noreferrer"
              title={sshTooltip(device.hostname, device, sshCred.username, sshCred.port)}
              className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 font-medium text-gray-700 dark:text-gray-200"
            >
              🔒 SSH
            </a>
            <div className="relative">
              <button onClick={() => setMenuOpen((o) => !o)} className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 font-medium text-gray-700 dark:text-gray-200">
                ⚙ Settings ▾
              </button>
              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setMenuOpen(false)} />
                  <div className="absolute right-0 mt-1 w-52 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-40 py-1">
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setEditing(true) }}>Edit Device Info</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setManagingCreds(true) }}>Manage Credentials</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setChangingCollector(true) }}>Change Collector</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setTelemetryConfig(true) }}>Telemetry Configuration</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setAddingManualLink(true) }}>Add Manual Link</button>
                    <div className="my-1 border-t border-gray-100 dark:border-gray-700" />
                    <button className={menuItem} onClick={collectNow} disabled={busy === 'collect'}>Collect Config Now</button>
                    <button className={menuItem} onClick={runDiscovery} disabled={busy === 'discover'}>Run Discovery</button>
                    <div className="my-1 border-t border-gray-100 dark:border-gray-700" />
                    <button className={clsx(menuItem, '!text-red-600 dark:!text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30')} onClick={() => { setMenuOpen(false); setDeleting(true) }}>Delete Device</button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-800 overflow-x-auto">
        {TABS.filter((t) => t.id !== 'wireless' || device.platform === 'unifi_ap').map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={clsx('px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors',
              tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800')}>
            {t.label}
          </button>
        ))}
      </div>
      </div>{/* end sticky header + tab bar */}

      {toast && (
        <div className={clsx('rounded-lg px-4 py-2 text-sm border', toast.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
          {toast.ok ? '✅' : '❌'} {toast.msg}
        </div>
      )}

      {/* Tab content */}
      {tab === 'overview' && <Overview device={device} onTab={setTab} onRefresh={load} onManageCredentials={() => setManagingCreds(true)} />}
      {tab === 'telemetry' && <Telemetry device={device} onConfigure={() => setTelemetryConfig(true)} refreshSignal={telemetryRefresh} />}
      {tab === 'wireless' && device.platform === 'unifi_ap' && <Wireless device={device} />}
      {tab === 'environment' && <Environment device={device} />}
      {tab === 'logs' && <Logs device={device} />}
      {tab === 'flows' && <Flows device={device} />}
      {tab === 'arpmac' && <ArpMac device={device} />}
      {tab === 'configuration' && <Configuration device={device} />}
      {tab === 'compliance' && <Compliance device={device} />}
      {tab === 'checks' && <ServiceChecks device={device} />}
      {tab === 'cve' && <CVE device={device} />}
      {tab === 'lifecycle' && <Lifecycle device={device} />}

      {editing && <DeviceEditModal device={device} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load() }} />}
      {managingCreds && <DeviceCredentialsPanel device={device} onClose={() => setManagingCreds(false)} onSaved={() => { setManagingCreds(false); load() }} />}
      {telemetryConfig && <TelemetryConfigPanel device={device} onClose={() => { setTelemetryConfig(false); setTelemetryRefresh((n) => n + 1) }} />}
      {changingCollector && <ChangeCollectorModal device={device} onClose={() => setChangingCollector(false)} onSaved={() => { setChangingCollector(false); load() }} />}
      {addingManualLink && <ManualLinkModal prefillDeviceA={device.id} onClose={() => setAddingManualLink(false)} onSaved={() => setAddingManualLink(false)} />}
      {deleting && (
        <Modal title="Delete device?" onClose={() => setDeleting(false)}
          footer={
            <>
              <button onClick={() => setDeleting(false)} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
              <button onClick={confirmDelete} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium">Delete</button>
            </>
          }>
          <p className="text-sm text-gray-700"><span className="font-medium">{device.hostname}</span> and its monitored interfaces, configs and telemetry settings will be permanently removed. This cannot be undone.</p>
        </Modal>
      )}
    </div>
  )
}

function relAge(iso?: string | null): string {
  if (!iso) return 'never'
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function ReachabilityIndicator({ device }: { device: Device }) {
  const reach = reachabilityOf(device)
  const dot = reach === 'reachable' ? 'bg-green-500' : reach === 'degraded' ? 'bg-yellow-500' : 'bg-red-500'
  const label = reach === 'unreachable' ? 'Unreachable' : reach === 'degraded' ? 'Degraded' : 'Reachable'
  const detail = reach === 'reachable' || reach === 'degraded'
    ? `checked ${relAge(device.last_reachability_check ?? device.last_seen)}`
    : `last seen ${relAge(device.last_seen)}`
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
      <span className={clsx('w-2 h-2 rounded-full', dot)} />
      {label} — {detail}
    </span>
  )
}

function ChangeCollectorModal({ device, onClose, onSaved }: { device: Device; onClose: () => void; onSaved: () => void }) {
  const [collectors, setCollectors] = useState<Collector[]>([])
  const [sel, setSel] = useState<number | ''>((device as { collector?: number | null }).collector ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { fetchCollectors().then(setCollectors).catch(() => {}) }, [])

  const save = async () => {
    setSaving(true); setError(null)
    try { await setDeviceCollector(device.id, sel === '' ? null : Number(sel)); onSaved() }
    catch { setError('Failed to update collector.'); setSaving(false) }
  }

  return (
    <Modal title={`Change collector — ${device.hostname}`} onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save'}</button>
        </>
      }>
      {error && <div className="mb-3 text-sm text-red-600 dark:text-red-400">{error}</div>}
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">Pick the collector that monitors this device. "Auto" uses the site default, then the global default.</p>
      <select className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        value={sel} onChange={(e) => setSel(e.target.value === '' ? '' : Number(e.target.value))}>
        <option value="">Auto (site / global default)</option>
        {collectors.map((c) => (
          <option key={c.id} value={c.id}>{c.name}{c.collector_ip ? ` (${c.collector_ip})` : ''}{c.is_default ? ' — default' : ''}</option>
        ))}
      </select>
      {device.collector_name && (
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Currently resolved: {device.collector_name}{device.collector_ip ? ` (${device.collector_ip})` : ''}</p>
      )}
    </Modal>
  )
}

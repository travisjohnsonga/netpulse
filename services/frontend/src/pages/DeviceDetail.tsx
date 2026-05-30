import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { api, fetchDevice, deleteDevice, discoverInterfaces, type DeviceDetail as Device } from '../api/client'
import Overview from './device/Overview'
import Telemetry from './device/Telemetry'
import Logs from './device/Logs'
import Configuration from './device/Configuration'
import Compliance from './device/Compliance'
import CVE from './device/CVE'
import Lifecycle from './device/Lifecycle'
import Modal from '../components/Modal'
import DeviceEditModal from '../components/DeviceEditModal'
import DeviceCredentialsPanel from '../components/DeviceCredentialsPanel'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'telemetry', label: 'Telemetry' },
  { id: 'logs', label: 'Logs' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'compliance', label: 'Compliance' },
  { id: 'cve', label: 'CVE' },
  { id: 'lifecycle', label: 'Lifecycle' },
] as const

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
  const [tab, setTab] = useState<string>('overview')

  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [managingCreds, setManagingCreds] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchDevice(deviceId)
      .then((d) => { setDevice(d); setError(null) })
      .catch(() => setError('Device not found or the API is unavailable.'))
      .finally(() => setLoading(false))
  }, [deviceId])

  useEffect(() => { load() }, [load])

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

  const menuItem = 'w-full text-left px-4 py-2 text-sm hover:bg-gray-50'

  return (
    <div className="space-y-4">
      {/* Breadcrumb + header */}
      <div>
        <Link to="/devices" className="text-sm text-blue-600 hover:text-blue-800">&larr; Devices</Link>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mt-2">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{device.hostname}</h1>
            <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[device.status] ?? 'bg-gray-100 text-gray-600')}>{device.status}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-500 font-mono">{device.ip_address}</span>
            <div className="relative">
              <button onClick={() => setMenuOpen((o) => !o)} className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 font-medium">
                ⚙ Settings ▾
              </button>
              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setMenuOpen(false)} />
                  <div className="absolute right-0 mt-1 w-52 bg-white border border-gray-200 rounded-lg shadow-lg z-40 py-1">
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setEditing(true) }}>Edit Device Info</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setManagingCreds(true) }}>Manage Credentials</button>
                    <button className={menuItem} onClick={() => { setMenuOpen(false); setTab('telemetry') }}>Polling Configuration</button>
                    <div className="my-1 border-t border-gray-100" />
                    <button className={menuItem} onClick={collectNow} disabled={busy === 'collect'}>Collect Config Now</button>
                    <button className={menuItem} onClick={runDiscovery} disabled={busy === 'discover'}>Run Discovery</button>
                    <div className="my-1 border-t border-gray-100" />
                    <button className={clsx(menuItem, 'text-red-600 hover:bg-red-50')} onClick={() => { setMenuOpen(false); setDeleting(true) }}>Delete Device</button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {toast && (
        <div className={clsx('rounded-lg px-4 py-2 text-sm border', toast.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
          {toast.ok ? '✅' : '❌'} {toast.msg}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={clsx('px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors',
              tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800')}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'overview' && <Overview device={device} onTab={setTab} onRefresh={load} onManageCredentials={() => setManagingCreds(true)} />}
      {tab === 'telemetry' && <Telemetry device={device} />}
      {tab === 'logs' && <Logs device={device} />}
      {tab === 'configuration' && <Configuration device={device} />}
      {tab === 'compliance' && <Compliance device={device} />}
      {tab === 'cve' && <CVE device={device} />}
      {tab === 'lifecycle' && <Lifecycle device={device} />}

      {editing && <DeviceEditModal device={device} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load() }} />}
      {managingCreds && <DeviceCredentialsPanel device={device} onClose={() => setManagingCreds(false)} onSaved={() => { setManagingCreds(false); load() }} />}
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

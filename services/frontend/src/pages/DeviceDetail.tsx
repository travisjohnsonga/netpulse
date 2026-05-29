import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { fetchDevice, type DeviceDetail as Device } from '../api/client'
import Overview from './device/Overview'
import CredentialsTab from './device/CredentialsTab'
import Telemetry from './device/Telemetry'
import Configuration from './device/Configuration'
import Compliance from './device/Compliance'
import CVE from './device/CVE'
import Lifecycle from './device/Lifecycle'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'credentials', label: 'Credentials' },
  { id: 'telemetry', label: 'Telemetry' },
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
  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<string>('overview')

  const load = useCallback(() => {
    setLoading(true)
    fetchDevice(deviceId)
      .then((d) => { setDevice(d); setError(null) })
      .catch(() => setError('Device not found or the API is unavailable.'))
      .finally(() => setLoading(false))
  }, [deviceId])

  useEffect(() => { load() }, [load])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !device) {
    return (
      <div className="space-y-4">
        <Link to="/devices" className="text-sm text-blue-600 hover:text-blue-800">&larr; Back to devices</Link>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">
          {error ?? 'Device not found.'}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Breadcrumb + header */}
      <div>
        <Link to="/devices" className="text-sm text-blue-600 hover:text-blue-800">&larr; Devices</Link>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mt-2">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{device.hostname}</h1>
            <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[device.status] ?? 'bg-gray-100 text-gray-600')}>
              {device.status}
            </span>
          </div>
          <span className="text-sm text-gray-500 font-mono">{device.ip_address}</span>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors',
              tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'overview' && <Overview device={device} onTab={setTab} />}
      {tab === 'credentials' && <CredentialsTab device={device} />}
      {tab === 'telemetry' && <Telemetry device={device} />}
      {tab === 'configuration' && <Configuration device={device} />}
      {tab === 'compliance' && <Compliance device={device} />}
      {tab === 'cve' && <CVE device={device} />}
      {tab === 'lifecycle' && <Lifecycle device={device} />}
    </div>
  )
}

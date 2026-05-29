import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { Link } from 'react-router-dom'
import {
  fetchCredentials, fetchCredential, setDeviceCredentialProfile, testCredential,
  type DeviceDetail, type CredentialProfileListItem, type CredentialProfile,
  type CredentialProtocol, type CredentialTestResult,
} from '../../api/client'

const PROTOCOL_LABELS: Record<CredentialProtocol, string> = {
  ssh: 'SSH', snmpv2c: 'SNMPv2c', snmpv3: 'SNMPv3',
  https: 'HTTPS/API', netconf: 'NETCONF', gnmi: 'gNMI',
}

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

export default function CredentialsTab({ device }: { device: DeviceDetail }) {
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [current, setCurrent] = useState<CredentialProfile | null>(null)
  const [assignedId, setAssignedId] = useState<number | null>(device.credential_profile)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [test, setTest] = useState<CredentialTestResult | 'running' | null>(null)

  const loadCurrent = useCallback((id: number | null) => {
    if (id == null) { setCurrent(null); return }
    fetchCredential(id).then(setCurrent).catch(() => setCurrent(null))
  }, [])

  useEffect(() => {
    setLoading(true)
    fetchCredentials()
      .then((p) => { setProfiles(p); setError(null) })
      .catch(() => setError('Failed to load credential profiles.'))
      .finally(() => setLoading(false))
    loadCurrent(device.credential_profile)
  }, [device.credential_profile, loadCurrent])

  const assign = async (id: number | null) => {
    setSaving(true); setError(null); setTest(null)
    try {
      await setDeviceCredentialProfile(device.id, id)
      setAssignedId(id)
      loadCurrent(id)
    } catch {
      setError('Failed to update the device credential profile.')
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    if (assignedId == null) return
    setTest('running')
    try { setTest(await testCredential(assignedId, device.ip_address)) }
    catch { setTest({ ip: device.ip_address, overall: 'failure', results: [] }) }
  }

  if (loading) return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  return (
    <div className="space-y-4">
      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
        <h3 className="text-sm font-semibold text-gray-800 mb-1">Credential Profile</h3>
        <p className="text-xs text-gray-500 mb-3">This device uses one profile covering every protocol it needs. Secrets stay in OpenBao.</p>

        <div className="flex flex-col sm:flex-row gap-2">
          <select
            className={inputCls}
            value={assignedId ?? ''}
            disabled={saving}
            onChange={(e) => assign(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">— No profile —</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>{p.name} ({p.enabled_protocols.map((x) => PROTOCOL_LABELS[x]).join(', ') || 'none'})</option>
            ))}
          </select>
          <button
            onClick={runTest}
            disabled={assignedId == null || test === 'running'}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50 shrink-0"
          >
            {test === 'running' ? 'Testing…' : 'Test All'}
          </button>
        </div>
        {profiles.length === 0 && (
          <p className="text-xs text-gray-400 mt-2">No profiles yet — <Link to="/settings/credentials" className="text-blue-600 hover:text-blue-800">create one in Settings → Credentials</Link>.</p>
        )}
      </div>

      {/* Current profile detail */}
      {current && (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-800">{current.name}</h3>
            <Link to="/settings/credentials" className="text-xs text-blue-600 hover:text-blue-800">Edit in Settings →</Link>
          </div>
          {current.description && <p className="text-xs text-gray-500 mb-3">{current.description}</p>}
          <div className="flex flex-wrap gap-1">
            {current.enabled_protocols.length === 0 && <span className="text-xs text-gray-400">No protocols enabled on this profile.</span>}
            {current.enabled_protocols.map((p) => (
              <span key={p} className="text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{PROTOCOL_LABELS[p]}</span>
            ))}
          </div>
        </div>
      )}

      {/* Test results */}
      {test && test !== 'running' && (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 space-y-2">
          <h3 className="text-sm font-semibold text-gray-800">Test results — {test.ip}</h3>
          {test.results.length === 0 ? (
            <p className="text-sm text-red-600">Test could not run. Ensure the profile has protocols enabled.</p>
          ) : test.results.map((r) => (
            <div key={r.protocol} className={clsx('flex items-center gap-2 rounded-md px-3 py-2 text-sm border', r.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
              <span>{r.success ? '✅' : '❌'}</span>
              <span className="font-medium">{r.label}</span>
              <span className="text-xs opacity-80 truncate">{r.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchCredentials, fetchCredential, setDeviceCredentialProfile, testCredential,
  type DeviceDetail, type CredentialProfileListItem, type CredentialProfile,
  type CredentialTestResult,
} from '../api/client'

export default function DeviceCredentialsPanel({ device, onClose, onSaved }: {
  device: DeviceDetail
  onClose: () => void
  onSaved: () => void
}) {
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(device.credential_profile)
  const [detail, setDetail] = useState<CredentialProfile | null>(null)
  const [test, setTest] = useState<CredentialTestResult | 'running' | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { fetchCredentials().then(setProfiles).catch(() => setError('Failed to load profiles.')) }, [])
  useEffect(() => {
    if (selectedId == null) { setDetail(null); return }
    fetchCredential(selectedId).then(setDetail).catch(() => setDetail(null))
  }, [selectedId])

  const caps = (p: CredentialProfile | null) => p ? [
    { on: p.ssh_enabled, label: `SSH${p.ssh_username ? ` (${p.ssh_username}, port ${p.ssh_port})` : ''}` },
    { on: p.snmpv3_enabled, label: `SNMPv3${p.snmpv3_username ? ` (${p.snmpv3_username}, ${p.snmpv3_auth_protocol}/${p.snmpv3_priv_protocol})` : ''}` },
    { on: p.snmpv2c_enabled, label: 'SNMPv2c' },
    { on: p.https_enabled, label: `HTTPS${p.https_auth_type ? ` (${p.https_auth_type})` : ''}` },
    { on: p.netconf_enabled, label: 'NETCONF' },
    { on: p.gnmi_enabled, label: 'gNMI' },
  ] : []

  const runTest = async () => {
    if (selectedId == null) return
    setTest('running'); setError(null)
    try { setTest(await testCredential(selectedId, device.ip_address)) }
    catch { setTest({ ip: device.ip_address, overall: 'failure', results: [] }) }
  }

  const save = async () => {
    setSaving(true); setError(null); setMsg(null)
    try { await setDeviceCredentialProfile(device.id, selectedId); setMsg('Saved'); onSaved() }
    catch { setError('Failed to save credential profile.') } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div className="bg-white w-full max-w-md h-full shadow-xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">Device Credentials</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {error && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{error}</div>}
          {msg && <div className="bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-sm text-green-700">{msg}</div>}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Credential Profile</label>
            <select
              className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={selectedId ?? ''} onChange={(e) => { setSelectedId(e.target.value ? Number(e.target.value) : null); setTest(null) }}>
              <option value="">— No profile —</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {profiles.length === 0 && (
              <p className="text-xs text-gray-400 mt-1">No profiles — <Link to="/settings/credentials" className="text-blue-600 hover:text-blue-800">create one</Link>.</p>
            )}
          </div>

          {detail && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">Profile capabilities</p>
              <ul className="space-y-1 text-sm">
                {caps(detail).map((c) => (
                  <li key={c.label} className={clsx('flex items-center gap-2', c.on ? 'text-gray-800' : 'text-gray-400')}>
                    <span>{c.on ? '✅' : '❌'}</span> {c.label}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-gray-500 mt-3">
                Last tested: {detail.last_tested ? new Date(detail.last_tested).toLocaleString() : 'never'}
                {detail.last_test_result !== 'untested' && ` · ${detail.last_test_result}`}
              </p>
            </div>
          )}

          {test && test !== 'running' && (
            <div className="space-y-1.5">
              {test.results.length === 0 ? <p className="text-xs text-red-600">No protocols to test.</p> :
                test.results.map((r) => (
                  <div key={r.protocol} className={clsx('text-xs', r.success ? 'text-green-700' : 'text-red-700')}>
                    {r.success ? '✅' : '❌'} {r.label}: {r.message}
                  </div>
                ))}
            </div>
          )}
        </div>

        <div className="flex gap-2 px-5 py-4 border-t border-gray-200">
          <button onClick={runTest} disabled={selectedId == null || test === 'running'} className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50">
            {test === 'running' ? 'Testing…' : 'Test Connection'}
          </button>
          <button onClick={save} disabled={saving} className="flex-1 px-3 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

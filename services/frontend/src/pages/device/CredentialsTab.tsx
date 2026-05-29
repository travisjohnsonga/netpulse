import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchDeviceCredentials, addDeviceCredential, removeDeviceCredential,
  fetchCredentials, testCredential,
  type DeviceDetail, type DeviceCredential, type CredentialProfileListItem,
  type CredentialPurpose, type CredentialTestResult,
} from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'

const PURPOSES: { id: CredentialPurpose; label: string }[] = [
  { id: 'snmp_polling', label: 'SNMP Polling' },
  { id: 'ssh_config', label: 'SSH (config push)' },
  { id: 'ssh_backup', label: 'SSH (config backup)' },
  { id: 'netconf', label: 'NETCONF' },
  { id: 'gnmi', label: 'gNMI' },
  { id: 'http_api', label: 'HTTP API' },
]
const PURPOSE_LABEL = Object.fromEntries(PURPOSES.map((p) => [p.id, p.label]))

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

export default function CredentialsTab({ device }: { device: DeviceDetail }) {
  const [links, setLinks] = useState<DeviceCredential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  // credential link id → test result (or 'running')
  const [tests, setTests] = useState<Record<number, CredentialTestResult | 'running'>>({})

  const load = useCallback(() => {
    setLoading(true)
    fetchDeviceCredentials(device.id)
      .then((data) => { setLinks(data); setError(null) })
      .catch(() => setError('Failed to load credential associations.'))
      .finally(() => setLoading(false))
  }, [device.id])

  useEffect(() => { load() }, [load])

  const runTest = async (link: DeviceCredential) => {
    setTests((t) => ({ ...t, [link.id]: 'running' }))
    try {
      const result = await testCredential(link.credential, device.ip_address)
      setTests((t) => ({ ...t, [link.id]: result }))
    } catch {
      setTests((t) => ({ ...t, [link.id]: { ip: device.ip_address, success: false, message: 'Test request failed.', latency_ms: null, port: 0 } }))
    }
  }

  const remove = async (link: DeviceCredential) => {
    try {
      await removeDeviceCredential(device.id, link.purpose)
      load()
    } catch {
      setError('Failed to remove association.')
    }
  }

  const usedPurposes = new Set(links.map((l) => l.purpose))

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm text-gray-500">Credential profiles bound to this device, by purpose. Secrets stay in OpenBao.</p>
        <button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shrink-0">+ Add Credential</button>
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 mb-4">{error}</div>}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : links.length === 0 ? (
          <EmptyState title="No credentials assigned" description="Associate a credential profile so NetPulse can poll, back up and manage this device." action={{ label: 'Add Credential', onClick: () => setAdding(true) }} icon="🔑" />
        ) : (
          <div className="divide-y divide-gray-100">
            {links.map((link) => {
              const test = tests[link.id]
              return (
                <div key={link.id} className="px-4 py-3">
                  <div className="flex items-center gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{PURPOSE_LABEL[link.purpose] ?? link.purpose}</span>
                        {link.is_primary && <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-purple-50 text-purple-700">Primary</span>}
                      </div>
                      <p className="font-medium text-gray-900 mt-1 truncate">{link.credential_name} <span className="text-xs font-normal text-gray-400">({link.credential_type})</span></p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        Last success: {link.last_success ? new Date(link.last_success).toLocaleString() : '—'}
                        {link.failure_count > 0 && <span className="text-red-500"> · {link.failure_count} failure{link.failure_count !== 1 ? 's' : ''}</span>}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button onClick={() => runTest(link)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">
                        {test === 'running' ? 'Testing…' : 'Test'}
                      </button>
                      <button onClick={() => remove(link)} className="px-2.5 py-1 text-xs border border-red-200 text-red-600 rounded-md hover:bg-red-50">Remove</button>
                    </div>
                  </div>
                  {test && test !== 'running' && (
                    <div className={clsx('mt-2 rounded-md px-3 py-2 text-xs border',
                      test.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
                      {test.success ? '✓' : '✗'} {test.message}{test.latency_ms != null ? ` (${test.latency_ms} ms)` : ''}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {adding && (
        <AddCredentialModal
          deviceId={device.id}
          deviceIp={device.ip_address}
          usedPurposes={usedPurposes}
          onClose={() => setAdding(false)}
          onAdded={() => { setAdding(false); load() }}
        />
      )}
    </div>
  )
}

function AddCredentialModal({ deviceId, deviceIp, usedPurposes, onClose, onAdded }: {
  deviceId: number
  deviceIp: string
  usedPurposes: Set<string>
  onClose: () => void
  onAdded: () => void
}) {
  const available = PURPOSES.filter((p) => !usedPurposes.has(p.id))
  const [purpose, setPurpose] = useState<CredentialPurpose>(available[0]?.id ?? 'snmp_polling')
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [credential, setCredential] = useState<number | null>(null)
  const [isPrimary, setIsPrimary] = useState(true)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchCredentials().then((p) => { setProfiles(p); if (p[0]) setCredential(p[0].id) }).catch(() => setErr('Failed to load credential profiles.'))
  }, [])

  const submit = async () => {
    if (!credential) { setErr('Select a credential profile.'); return }
    setSaving(true); setErr(null)
    try {
      await addDeviceCredential(deviceId, { credential, purpose, is_primary: isPrimary, notes })
      onAdded()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to add credential.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title="Add Credential"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={submit} disabled={saving || available.length === 0} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Adding…' : 'Add'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
        {available.length === 0 ? (
          <p className="text-sm text-gray-500">All credential purposes are already assigned. Remove one first to reassign.</p>
        ) : (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Purpose</label>
              <select className={inputCls} value={purpose} onChange={(e) => setPurpose(e.target.value as CredentialPurpose)}>
                {available.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Credential profile</label>
              <select className={inputCls} value={credential ?? ''} onChange={(e) => setCredential(Number(e.target.value))}>
                {profiles.length === 0 && <option value="">No profiles — create one in Settings → Credentials</option>}
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.credential_type})</option>)}
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} />
              Primary credential for this purpose
            </label>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Notes (optional)</label>
              <input className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            <p className="text-xs text-gray-400">Tests run against this device's IP ({deviceIp}).</p>
          </>
        )}
      </div>
    </Modal>
  )
}

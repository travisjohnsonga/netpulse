import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchSites, fetchCredentials, createDevice, addDeviceCredential, testCredential,
  type Site, type CredentialProfileListItem, type CredentialPurpose,
  type DeviceDetail, type CredentialTestResult,
} from '../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

const PLATFORMS = [
  { value: 'ios', label: 'Cisco IOS' },
  { value: 'ios_xe', label: 'Cisco IOS-XE' },
  { value: 'ios_xr', label: 'Cisco IOS-XR' },
  { value: 'nxos', label: 'Cisco NX-OS' },
  { value: 'eos', label: 'Arista EOS' },
  { value: 'junos', label: 'Juniper JunOS' },
  { value: 'sonic', label: 'SONiC' },
  { value: 'other', label: 'Other' },
]

const PROTOCOLS: { key: CredentialPurpose; label: string }[] = [
  { key: 'snmp_polling', label: 'SNMP' },
  { key: 'ssh_config', label: 'SSH' },
  { key: 'netconf', label: 'NETCONF' },
  { key: 'gnmi', label: 'gNMI' },
  { key: 'http_api', label: 'HTTP' },
]

const TELEMETRY_FEATURES = [
  { key: 'snmp', label: 'SNMP polling', snippet: 'snmp-server community netpulse RO\nsnmp-server host 10.0.0.10 version 2c netpulse' },
  { key: 'syslog', label: 'Syslog forwarding', snippet: 'logging host 10.0.0.10 transport udp port 514' },
  { key: 'gnmi', label: 'gNMI dial-out', snippet: 'telemetry\n model-driven\n  destination-group netpulse\n   address 10.0.0.10 57400 protocol grpc' },
  { key: 'netflow', label: 'NetFlow export', snippet: 'flow exporter netpulse\n destination 10.0.0.10\n transport udp 2055' },
]

const STEPS = ['Basic Info', 'Platform', 'Credentials', 'Telemetry', 'Confirm']

interface ProtoSel { enabled: boolean; credential: number | null }

export default function DeviceAddModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [step, setStep] = useState(0)

  // Step 1 — basic info
  const [hostname, setHostname] = useState('')
  const [ip, setIp] = useState('')
  const [siteId, setSiteId] = useState<number | ''>('')
  const [role, setRole] = useState('')
  const [tags, setTags] = useState('')
  const [sites, setSites] = useState<Site[]>([])

  // Step 2 — platform
  const [platform, setPlatform] = useState('other')
  const [vendor, setVendor] = useState('')
  const [detecting, setDetecting] = useState(false)
  const [detectNote, setDetectNote] = useState<string | null>(null)

  // Step 3 — credentials
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [sel, setSel] = useState<Record<string, ProtoSel>>(
    Object.fromEntries(PROTOCOLS.map((p) => [p.key, { enabled: false, credential: null }])),
  )
  const [tests, setTests] = useState<Record<string, CredentialTestResult | 'running'>>({})

  // Step 4 — telemetry
  const [features, setFeatures] = useState<Set<string>>(new Set(['snmp', 'syslog']))

  // Step 5 — result
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<DeviceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSites().then(setSites).catch(() => {})
    fetchCredentials().then(setProfiles).catch(() => {})
  }, [])

  const reset = () => {
    setStep(0); setHostname(''); setIp(''); setSiteId(''); setRole(''); setTags('')
    setPlatform('other'); setVendor(''); setDetectNote(null)
    setSel(Object.fromEntries(PROTOCOLS.map((p) => [p.key, { enabled: false, credential: null }])))
    setTests({}); setFeatures(new Set(['snmp', 'syslog'])); setCreated(null); setError(null)
  }

  const autoDetect = () => {
    setDetecting(true); setDetectNote(null)
    // No live probe endpoint yet — illustrate the flow, then prompt confirmation.
    setTimeout(() => {
      setPlatform('ios_xe'); setVendor('Cisco')
      setDetectNote('Suggested Cisco IOS-XE from SSH banner heuristics (simulated). Confirm or adjust below.')
      setDetecting(false)
    }, 700)
  }

  const testAll = async () => {
    for (const p of PROTOCOLS) {
      const s = sel[p.key]
      if (!s.enabled || !s.credential) continue
      setTests((t) => ({ ...t, [p.key]: 'running' }))
      try {
        const r = await testCredential(s.credential, ip)
        setTests((t) => ({ ...t, [p.key]: r }))
      } catch {
        setTests((t) => ({ ...t, [p.key]: { ip, success: false, message: 'Test failed', latency_ms: null, port: 0 } }))
      }
    }
  }

  const submit = async () => {
    setCreating(true); setError(null)
    const noteParts: string[] = []
    if (role) noteParts.push(`Role: ${role}`)
    if (tags) noteParts.push(`Tags: ${tags}`)
    if (features.size) noteParts.push(`Telemetry: ${[...features].join(', ')}`)
    try {
      const device = await createDevice({
        hostname: hostname.trim(),
        ip_address: ip.trim(),
        platform,
        vendor,
        site: siteId === '' ? null : Number(siteId),
        status: 'active',
        notes: noteParts.join('\n'),
      })
      // Associate selected credentials.
      for (const p of PROTOCOLS) {
        const s = sel[p.key]
        if (s.enabled && s.credential) {
          try { await addDeviceCredential(device.id, { credential: s.credential, purpose: p.key, is_primary: true }) } catch { /* continue */ }
        }
      }
      setCreated(device)
      setStep(4)
      onCreated()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setError(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to create device.')
    } finally {
      setCreating(false)
    }
  }

  const canNext =
    step === 0 ? hostname.trim() !== '' && ip.trim() !== '' : true

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        {/* Header + stepper */}
        <div className="px-6 py-4 border-b border-gray-200">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-gray-900">Add Device</h2>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
          </div>
          <div className="flex items-center gap-1 mt-3">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-1 flex-1 last:flex-none">
                <div className={clsx('flex items-center gap-1.5 text-xs font-medium', i <= step ? 'text-blue-700' : 'text-gray-400')}>
                  <span className={clsx('w-5 h-5 rounded-full flex items-center justify-center text-[11px]',
                    i < step ? 'bg-blue-600 text-white' : i === step ? 'bg-blue-100 text-blue-700 ring-2 ring-blue-600' : 'bg-gray-100 text-gray-400')}>
                    {i < step ? '✓' : i + 1}
                  </span>
                  <span className="hidden sm:inline">{label}</span>
                </div>
                {i < STEPS.length - 1 && <div className={clsx('h-px flex-1', i < step ? 'bg-blue-600' : 'bg-gray-200')} />}
              </div>
            ))}
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto">
          {error && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 mb-4">{error}</div>}

          {step === 0 && (
            <div className="space-y-3">
              <Field label="Hostname *"><input className={inputCls} value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="core-rtr-01" /></Field>
              <Field label="IP Address *"><input className={inputCls} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.0.1" /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Site">
                  <select className={inputCls} value={siteId} onChange={(e) => setSiteId(e.target.value === '' ? '' : Number(e.target.value))}>
                    <option value="">— None —</option>
                    {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </Field>
                <Field label="Role"><input className={inputCls} value={role} onChange={(e) => setRole(e.target.value)} placeholder="core / edge / access" /></Field>
              </div>
              <Field label="Tags (comma separated)"><input className={inputCls} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="dc1, critical" /></Field>
              <p className="text-xs text-gray-400">Role & tags are saved to the device notes until dedicated fields land.</p>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-3">
              <button onClick={autoDetect} disabled={detecting} className="px-4 py-2 border border-blue-300 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-50 disabled:opacity-50">
                {detecting ? 'Detecting…' : '🔍 Auto-detect platform'}
              </button>
              {detectNote && <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-800">{detectNote}</div>}
              <Field label="Platform">
                <select className={inputCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
                  {PLATFORMS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </Field>
              <Field label="Vendor"><input className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="Cisco" /></Field>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-500">Bind existing credential profiles per protocol. Create profiles in Settings → Credentials.</p>
              {PROTOCOLS.map((p) => {
                const s = sel[p.key]
                const test = tests[p.key]
                return (
                  <div key={p.key} className="border border-gray-200 rounded-lg p-3">
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-2 text-sm font-medium text-gray-700 w-24">
                        <input type="checkbox" checked={s.enabled} onChange={(e) => setSel((x) => ({ ...x, [p.key]: { ...x[p.key], enabled: e.target.checked } }))} />
                        {p.label}
                      </label>
                      <select
                        className={clsx(inputCls, 'flex-1', !s.enabled && 'opacity-50')}
                        disabled={!s.enabled}
                        value={s.credential ?? ''}
                        onChange={(e) => setSel((x) => ({ ...x, [p.key]: { ...x[p.key], credential: e.target.value ? Number(e.target.value) : null } }))}
                      >
                        <option value="">Select profile…</option>
                        {profiles.map((pr) => <option key={pr.id} value={pr.id}>{pr.name} ({pr.credential_type})</option>)}
                      </select>
                    </div>
                    {test && test !== 'running' && (
                      <p className={clsx('text-xs mt-2', test.success ? 'text-green-600' : 'text-red-600')}>{test.success ? '✓' : '✗'} {test.message}</p>
                    )}
                    {test === 'running' && <p className="text-xs text-gray-400 mt-2">Testing…</p>}
                  </div>
                )
              })}
              <button onClick={testAll} disabled={!ip} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">Test All</button>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-500">Enable telemetry sources. Apply the matching CLI on the device.</p>
              {TELEMETRY_FEATURES.map((f) => (
                <div key={f.key} className="border border-gray-200 rounded-lg p-3">
                  <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
                    <input
                      type="checkbox"
                      checked={features.has(f.key)}
                      onChange={(e) => setFeatures((s) => { const n = new Set(s); e.target.checked ? n.add(f.key) : n.delete(f.key); return n })}
                    />
                    {f.label}
                  </label>
                  {features.has(f.key) && (
                    <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-md p-2 mt-2 overflow-x-auto whitespace-pre-wrap">{f.snippet}</pre>
                  )}
                </div>
              ))}
            </div>
          )}

          {step === 4 && created && (
            <div className="text-center py-6">
              <div className="w-14 h-14 mx-auto rounded-full bg-green-100 text-green-600 flex items-center justify-center text-2xl mb-3">✓</div>
              <h3 className="text-lg font-semibold text-gray-900">{created.hostname} added</h3>
              <p className="text-sm text-gray-500 mt-1">{created.ip_address} · {PLATFORMS.find((p) => p.value === created.platform)?.label}</p>
              <div className="flex gap-3 justify-center mt-6">
                <Link to={`/devices/${created.id}`} onClick={onClose} className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">View Device</Link>
                <button onClick={reset} className="px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Add Another</button>
              </div>
            </div>
          )}
        </div>

        {/* Footer nav (hidden on success step) */}
        {step < 4 && (
          <div className="flex gap-3 px-6 py-4 border-t border-gray-200">
            <button
              onClick={() => (step === 0 ? onClose() : setStep((s) => s - 1))}
              className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50"
            >
              {step === 0 ? 'Cancel' : 'Back'}
            </button>
            {step < 3 ? (
              <button
                onClick={() => setStep((s) => s + 1)}
                disabled={!canNext}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
              >
                Continue
              </button>
            ) : (
              <button
                onClick={submit}
                disabled={creating}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
              >
                {creating ? 'Creating…' : 'Create Device'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {children}
    </div>
  )
}

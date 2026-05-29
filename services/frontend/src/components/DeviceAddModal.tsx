import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchSites, createSite, fetchCredentials, createDevice, detectPlatform, testCredential,
  type Site, type CredentialProfileListItem, type DeviceDetail,
  type DetectPlatformResult, type CredentialTestResult,
} from '../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

const PLATFORMS = [
  { value: 'ios', label: 'Cisco IOS', vendor: 'cisco' },
  { value: 'ios_xe', label: 'Cisco IOS-XE', vendor: 'cisco' },
  { value: 'ios_xr', label: 'Cisco IOS-XR', vendor: 'cisco' },
  { value: 'nxos', label: 'Cisco NX-OS', vendor: 'cisco' },
  { value: 'asa', label: 'Cisco ASA', vendor: 'cisco' },
  { value: 'eos', label: 'Arista EOS', vendor: 'arista' },
  { value: 'junos', label: 'Juniper JunOS', vendor: 'juniper' },
  { value: 'fortios', label: 'FortiOS', vendor: 'fortinet' },
  { value: 'panos', label: 'PAN-OS', vendor: 'paloalto' },
  { value: 'vyos', label: 'VyOS', vendor: 'vyos' },
  { value: 'linux', label: 'Linux', vendor: 'linux' },
  { value: 'other', label: 'Other', vendor: '' },
]

const ROLES = ['access', 'distribution', 'core', 'wan-edge', 'firewall']

function vendorFamily(platform: string): 'cisco' | 'juniper' | 'arista' | 'generic' {
  if (platform.startsWith('ios') || platform === 'nxos' || platform === 'asa') return 'cisco'
  if (platform === 'junos') return 'juniper'
  if (platform === 'eos') return 'arista'
  return 'generic'
}

const SNIPPETS: Record<string, Record<string, string>> = {
  cisco: {
    snmp: 'snmp-server community netpulse RO\nsnmp-server host 10.0.0.10 version 2c netpulse',
    syslog: 'logging host 10.0.0.10\nlogging trap informational',
    gnmi: 'telemetry ietf subscription 1\n stream yang-push\n receiver ip address 10.0.0.10 57400 protocol grpc-tcp',
    netflow: 'flow exporter netpulse\n destination 10.0.0.10\n transport udp 2055',
  },
  juniper: {
    snmp: 'set snmp community netpulse authorization read-only\nset snmp trap-group netpulse targets 10.0.0.10',
    syslog: 'set system syslog host 10.0.0.10 any info',
    gnmi: 'set system services extension-service request-response grpc clear-text port 57400',
    netflow: 'set services flow-monitoring version-ipfix template netpulse',
  },
  arista: {
    snmp: 'snmp-server community netpulse ro\nsnmp-server host 10.0.0.10 version 2c netpulse',
    syslog: 'logging host 10.0.0.10\nlogging level informational',
    gnmi: 'management api gnmi\n transport grpc default',
    netflow: 'sflow destination 10.0.0.10\nsflow run',
  },
  generic: {
    snmp: '# Configure SNMP read-only community "netpulse" pointing at 10.0.0.10',
    syslog: '# Forward syslog to 10.0.0.10:514',
    gnmi: '# Enable gNMI dial-out to 10.0.0.10:57400',
    netflow: '# Export NetFlow/sFlow to 10.0.0.10:2055',
  },
}

const TELEMETRY_FEATURES = [
  { key: 'snmp', label: 'SNMP polling' },
  { key: 'syslog', label: 'Syslog' },
  { key: 'gnmi', label: 'gNMI streaming' },
  { key: 'netflow', label: 'NetFlow / sFlow' },
]

const DETECT_ERRORS: Record<string, string> = {
  timeout: 'Could not connect: timeout',
  auth_failed: 'Authentication failed — check the SSH credential',
  unknown: 'Could not identify the platform',
  ssh_not_enabled: 'The selected profile has no SSH enabled',
  request_failed: 'Detection request failed',
}

// New order: Basic Info → Credentials → Platform → Telemetry → Confirm.
const STEPS = ['Basic Info', 'Credentials', 'Platform', 'Telemetry', 'Confirm']

export default function DeviceAddModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [step, setStep] = useState(0)

  // Step 1 — Basic info
  const [hostname, setHostname] = useState('')
  const [ip, setIp] = useState('')
  const [mgmtIp, setMgmtIp] = useState('')
  const [siteId, setSiteId] = useState<number | ''>('')
  const [role, setRole] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [sites, setSites] = useState<Site[]>([])
  const [addingSite, setAddingSite] = useState(false)
  const [newSite, setNewSite] = useState('')

  // Step 2 — Credentials
  const [profiles, setProfiles] = useState<CredentialProfileListItem[]>([])
  const [credentialId, setCredentialId] = useState<number | null>(null)
  const [credTest, setCredTest] = useState<CredentialTestResult | 'running' | null>(null)

  // Step 3 — Platform
  const [platform, setPlatform] = useState('other')
  const [vendor, setVendor] = useState('')
  const [osVersion, setOsVersion] = useState('')
  const [model, setModel] = useState('')
  const [serial, setSerial] = useState('')
  const [detecting, setDetecting] = useState(false)
  const [detect, setDetect] = useState<DetectPlatformResult | null>(null)
  const [manualOpen, setManualOpen] = useState(false)

  // Step 4 — Telemetry
  const [features, setFeatures] = useState<Set<string>>(new Set(['snmp', 'syslog']))

  // Step 5 — result
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<DeviceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadProfiles = () => fetchCredentials().then(setProfiles).catch(() => {})
  useEffect(() => { fetchSites().then(setSites).catch(() => {}); loadProfiles() }, [])

  const selectedProfile = profiles.find((p) => p.id === credentialId)
  const hasSSH = !!selectedProfile?.enabled_protocols.includes('ssh')

  const reset = () => {
    setStep(0); setHostname(''); setIp(''); setMgmtIp(''); setSiteId(''); setRole(''); setTags([]); setTagInput('')
    setCredentialId(null); setCredTest(null)
    setPlatform('other'); setVendor(''); setOsVersion(''); setModel(''); setSerial(''); setDetect(null); setManualOpen(false)
    setFeatures(new Set(['snmp', 'syslog'])); setCreated(null); setError(null)
  }

  const addTag = () => {
    const t = tagInput.trim()
    if (t && !tags.includes(t)) setTags((x) => [...x, t])
    setTagInput('')
  }

  const addSite = async () => {
    if (!newSite.trim()) return
    try {
      const s = await createSite({ name: newSite.trim() })
      setSites((x) => [...x, s]); setSiteId(s.id); setNewSite(''); setAddingSite(false)
    } catch { setError('Failed to create site.') }
  }

  const runDetect = async () => {
    if (credentialId == null || !ip.trim()) return
    setDetecting(true); setDetect(null)
    try {
      setDetect(await detectPlatform(ip.trim(), credentialId))
    } catch {
      setDetect({ detected: false, error: 'request_failed' })
    } finally {
      setDetecting(false)
    }
  }

  const applyDetected = () => {
    if (!detect?.detected) return
    if (detect.vendor) setVendor(detect.vendor)
    if (detect.platform) setPlatform(detect.platform)
    if (detect.os_version) setOsVersion(detect.os_version)
    if (detect.model) setModel(detect.model)
    if (detect.serial) setSerial(detect.serial)
    if (detect.hostname) setHostname(detect.hostname)
  }

  const testCred = async () => {
    if (credentialId == null) return
    setCredTest('running')
    try { setCredTest(await testCredential(credentialId, ip.trim())) }
    catch { setCredTest({ ip, overall: 'failure', results: [] }) }
  }

  const submit = async () => {
    setCreating(true); setError(null)
    const noteParts: string[] = []
    if (role) noteParts.push(`Role: ${role}`)
    if (tags.length) noteParts.push(`Tags: ${tags.join(', ')}`)
    if (features.size) noteParts.push(`Telemetry: ${[...features].join(', ')}`)
    try {
      const device = await createDevice({
        hostname: hostname.trim(),
        ip_address: ip.trim(),
        management_ip: mgmtIp.trim() || null,
        platform, vendor,
        os_version: osVersion,
        model,
        serial_number: serial,
        site: siteId === '' ? null : Number(siteId),
        credential_profile: credentialId,
        status: 'active',
        notes: noteParts.join('\n'),
      })
      setCreated(device); setStep(4); onCreated()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setError(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to create device.')
    } finally { setCreating(false) }
  }

  const canNext = step === 0 ? hostname.trim() !== '' && ip.trim() !== '' : true
  const fam = vendorFamily(platform)

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

          {/* Step 1 — Basic Info */}
          {step === 0 && (
            <div className="space-y-3">
              <Field label="Hostname *"><input className={inputCls} value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="core-rtr-01" /></Field>
              <Row>
                <Field label="IP Address *"><input className={inputCls} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.0.1" /></Field>
                <Field label="Management IP"><input className={inputCls} value={mgmtIp} onChange={(e) => setMgmtIp(e.target.value)} placeholder="optional" /></Field>
              </Row>
              <Row>
                <Field label="Site">
                  {addingSite ? (
                    <div className="flex gap-2">
                      <input className={inputCls} value={newSite} onChange={(e) => setNewSite(e.target.value)} placeholder="New site name" />
                      <button onClick={addSite} className="px-3 py-2 text-sm bg-blue-600 text-white rounded-lg shrink-0">Add</button>
                    </div>
                  ) : (
                    <div className="flex gap-2">
                      <select className={inputCls} value={siteId} onChange={(e) => setSiteId(e.target.value === '' ? '' : Number(e.target.value))}>
                        <option value="">— None —</option>
                        {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select>
                      <button onClick={() => setAddingSite(true)} className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 shrink-0">+ New</button>
                    </div>
                  )}
                </Field>
                <Field label="Role">
                  <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value)}>
                    <option value="">— Select —</option>
                    {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </Field>
              </Row>
              <Field label="Tags">
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {tags.map((t) => (
                    <span key={t} className="inline-flex items-center gap-1 bg-gray-100 text-gray-700 text-xs px-2 py-1 rounded-md">
                      {t}<button onClick={() => setTags((x) => x.filter((v) => v !== t))} className="text-gray-400 hover:text-gray-700">×</button>
                    </span>
                  ))}
                </div>
                <input className={inputCls} value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag() } }}
                  placeholder="Type a tag and press Enter" />
              </Field>
            </div>
          )}

          {/* Step 2 — Credentials */}
          {step === 1 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-500">How should NetPulse connect to this device? Pick one profile covering every protocol it needs. SSH is required for platform auto-detection.</p>
              <div className="flex gap-2">
                <select className={inputCls} value={credentialId ?? ''} onChange={(e) => { setCredentialId(e.target.value ? Number(e.target.value) : null); setCredTest(null); setDetect(null) }}>
                  <option value="">— No profile —</option>
                  {profiles.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.enabled_protocols.join(', ') || 'none'})</option>)}
                </select>
                <button onClick={loadProfiles} title="Refresh" className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 shrink-0">↻</button>
              </div>
              {credentialId != null && !hasSSH && (
                <p className="text-xs text-amber-600">⚠ This profile has no SSH — platform auto-detection won't be available.</p>
              )}
              <a href="/settings/credentials" target="_blank" rel="noreferrer" className="inline-block text-xs text-blue-600 hover:text-blue-800">+ New profile (opens Settings → Credentials)</a>
              <div>
                <button onClick={testCred} disabled={credentialId == null || credTest === 'running'} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
                  {credTest === 'running' ? 'Testing…' : 'Test All'}
                </button>
              </div>
              {credTest && credTest !== 'running' && (
                <div className="space-y-1.5">
                  {credTest.results.length === 0 ? <p className="text-xs text-red-600">No protocols to test on this profile.</p> :
                    credTest.results.map((r) => (
                      <div key={r.protocol} className={clsx('text-xs', r.success ? 'text-green-700' : 'text-red-700')}>
                        {r.success ? '✅' : '❌'} {r.label}: {r.message}
                      </div>
                    ))}
                </div>
              )}
            </div>
          )}

          {/* Step 3 — Platform */}
          {step === 2 && (
            <div className="space-y-4">
              {!hasSSH ? (
                <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-3 text-sm text-amber-800">
                  Select an SSH credential profile in the previous step to enable auto-detection.
                </div>
              ) : (
                <button onClick={runDetect} disabled={detecting} className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-semibold">
                  {detecting ? 'Connecting to device…' : '🔍 Auto-Detect Platform'}
                </button>
              )}

              {detect && detect.detected && (
                <div className={clsx('rounded-lg px-4 py-3 text-sm border',
                  detect.confidence === 'low' ? 'bg-yellow-50 border-yellow-200 text-yellow-800' : 'bg-green-50 border-green-200 text-green-800')}>
                  <p className="font-medium">
                    {detect.confidence === 'low' ? '⚠️ Best guess' : '✅ Detected'}: {vendorLabel(detect.vendor)} {platformLabel(detect.platform)}
                    {detect.os_version ? ` ${detect.os_version}` : ''}
                    {detect.confidence === 'low' && ' (low confidence)'}
                  </p>
                  <div className="text-xs mt-1 space-y-0.5 opacity-90">
                    {detect.model && <div>Model: {detect.model}</div>}
                    {detect.hostname && <div>Hostname: {detect.hostname}</div>}
                    {detect.serial && <div>Serial: {detect.serial}</div>}
                  </div>
                  {detect.confidence === 'low' && <p className="text-xs mt-1">Please verify the platform selection below.</p>}
                  <button onClick={applyDetected} className="mt-2 px-3 py-1.5 text-xs bg-white border border-current rounded-md font-medium hover:bg-opacity-80">Use these values</button>
                </div>
              )}

              {detect && !detect.detected && (
                <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
                  ❌ {DETECT_ERRORS[detect.error ?? ''] ?? detect.error ?? 'Detection failed'}
                  {detect.best_guess && <div className="text-xs mt-1">Best guess: {detect.best_guess}</div>}
                  <div className="text-xs mt-1">Check the IP and credentials, or set the platform manually below.</div>
                </div>
              )}

              {/* Manual override */}
              <div className="border-t border-gray-100 pt-3">
                <button onClick={() => setManualOpen((o) => !o)} className="text-sm font-medium text-gray-700 hover:text-gray-900">
                  {manualOpen ? '▾' : '▸'} Override manually
                </button>
                {(manualOpen || platform !== 'other' || vendor) && (
                  <div className="space-y-3 mt-3">
                    <Row>
                      <Field label="Vendor"><input className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="cisco" /></Field>
                      <Field label="Platform">
                        <select className={inputCls} value={platform} onChange={(e) => {
                          setPlatform(e.target.value)
                          const v = PLATFORMS.find((p) => p.value === e.target.value)?.vendor
                          if (v) setVendor(v)
                        }}>
                          {PLATFORMS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                        </select>
                      </Field>
                    </Row>
                    <Row>
                      <Field label="OS Version"><input className={inputCls} value={osVersion} onChange={(e) => setOsVersion(e.target.value)} /></Field>
                      <Field label="Model"><input className={inputCls} value={model} onChange={(e) => setModel(e.target.value)} /></Field>
                    </Row>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Step 4 — Telemetry */}
          {step === 3 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-500">Enable telemetry sources, then apply the matching CLI for <strong>{PLATFORMS.find((p) => p.value === platform)?.label}</strong>.</p>
              {TELEMETRY_FEATURES.map((f) => (
                <div key={f.key} className="border border-gray-200 rounded-lg p-3">
                  <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
                    <input type="checkbox" checked={features.has(f.key)} onChange={(e) => setFeatures((s) => { const n = new Set(s); e.target.checked ? n.add(f.key) : n.delete(f.key); return n })} />
                    {f.label}
                  </label>
                  {features.has(f.key) && (
                    <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-md p-2 mt-2 overflow-x-auto whitespace-pre-wrap">{SNIPPETS[fam][f.key]}</pre>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Step 5 — Confirm */}
          {step === 4 && created && (
            <div className="text-center py-6">
              <div className="w-14 h-14 mx-auto rounded-full bg-green-100 text-green-600 flex items-center justify-center text-2xl mb-3">✓</div>
              <h3 className="text-lg font-semibold text-gray-900">{created.hostname} added</h3>
              <p className="text-sm text-gray-500 mt-1">{created.ip_address} · {PLATFORMS.find((p) => p.value === created.platform)?.label}</p>
              <div className="flex gap-3 justify-center mt-6">
                <Link to={`/devices/${created.id}`} onClick={onClose} className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">View Device</Link>
                <button onClick={reset} className="px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Add Another Device</button>
              </div>
            </div>
          )}
        </div>

        {/* Footer nav */}
        {step < 4 && (
          <div className="flex gap-3 px-6 py-4 border-t border-gray-200">
            <button onClick={() => (step === 0 ? onClose() : setStep((s) => s - 1))} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">
              {step === 0 ? 'Cancel' : 'Back'}
            </button>
            {step < 3 ? (
              <button onClick={() => setStep((s) => s + 1)} disabled={!canNext} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">Continue</button>
            ) : (
              <button onClick={submit} disabled={creating} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{creating ? 'Creating…' : 'Create Device'}</button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function vendorLabel(v?: string) {
  return v ? v.charAt(0).toUpperCase() + v.slice(1) : ''
}
function platformLabel(p?: string) {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p ?? ''
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex-1 min-w-0"><label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>{children}</div>
}
function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col sm:flex-row gap-3">{children}</div>
}

import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchCredentials, fetchCredential, createCredential, updateCredential,
  deleteCredential, testCredential,
  type CredentialProfileListItem, type CredentialProfile,
  type CredentialProtocol, type CredentialTestResult,
} from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'

const PROTOCOL_LABELS: Record<CredentialProtocol, string> = {
  ssh: 'SSH', snmpv2c: 'SNMPv2c', snmpv3: 'SNMPv3',
  https: 'HTTPS/API', netconf: 'NETCONF', gnmi: 'gNMI',
}
const PROTOCOL_ORDER: CredentialProtocol[] = ['ssh', 'snmpv3', 'snmpv2c', 'https', 'netconf', 'gnmi']

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'
const VAULT_NOTE = '🔒 Stored securely in OpenBao'

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Credentials() {
  const [items, setItems] = useState<CredentialProfileListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<CredentialProfile | 'new' | null>(null)
  const [testing, setTesting] = useState<CredentialProfileListItem | null>(null)
  const [deleting, setDeleting] = useState<CredentialProfileListItem | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchCredentials()
      .then((d) => { setItems(d); setError(null) })
      .catch(() => setError('Failed to load credential profiles. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const openEdit = async (id: number) => {
    try { setEditing(await fetchCredential(id)) } catch { setError('Failed to load profile.') }
  }

  return (
    <div>
      <SectionHeader
        title="Credentials"
        description="Multi-protocol credential profiles. Enable the protocols a device needs; secrets are stored in OpenBao."
        action={
          <button onClick={() => setEditing('new')} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ New Profile</button>
        }
      />

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 mb-4">{error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200">
          <EmptyState title="No credential profiles yet" description="Create a profile and enable the protocols (SSH, SNMP, HTTPS, NETCONF, gNMI) your devices use." action={{ label: 'New Profile', onClick: () => setEditing('new') }} icon="🔑" />
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden divide-y divide-gray-100">
          {items.map((row) => (
            <div key={row.id} className="flex items-center gap-4 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-medium text-gray-900 truncate">{row.name}</p>
                <div className="flex flex-wrap gap-1 mt-1">
                  {row.enabled_protocols.length === 0 && <span className="text-xs text-gray-400">No protocols enabled</span>}
                  {row.enabled_protocols.map((p) => (
                    <span key={p} className="text-xs font-medium px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{PROTOCOL_LABELS[p]}</span>
                  ))}
                </div>
              </div>
              <span className="text-xs text-gray-500 whitespace-nowrap">{row.device_count} device{row.device_count !== 1 ? 's' : ''}</span>
              <TestBadge result={row.last_test_result} />
              <div className="flex items-center gap-2">
                <button onClick={() => setTesting(row)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Test</button>
                <button onClick={() => openEdit(row.id)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Edit</button>
                <button onClick={() => setDeleting(row)} className="px-2.5 py-1 text-xs border border-red-200 text-red-600 rounded-md hover:bg-red-50">Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <CredentialModal profile={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
      )}
      {testing && <TestModal profile={testing} onClose={() => setTesting(null)} />}
      {deleting && <DeleteModal profile={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); load() }} />}
    </div>
  )
}

function TestBadge({ result }: { result: string }) {
  const map: Record<string, string> = {
    success: 'bg-green-100 text-green-700',
    partial: 'bg-yellow-100 text-yellow-700',
    failure: 'bg-red-100 text-red-700',
    untested: 'bg-gray-100 text-gray-500',
  }
  const label: Record<string, string> = { success: 'Tested OK', partial: 'Partial', failure: 'Test failed', untested: 'Untested' }
  return <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${map[result] ?? map.untested}`}>{label[result] ?? 'Untested'}</span>
}

// ── Create / Edit modal ──────────────────────────────────────────────────────

type Form = Record<string, string | number | boolean>

const DEFAULTS: Form = {
  name: '', description: '',
  ssh_enabled: false, ssh_username: '', ssh_auth_method: 'password', ssh_port: 22,
  snmpv2c_enabled: false, snmpv2c_port: 161,
  snmpv3_enabled: false, snmpv3_username: '', snmpv3_security_level: 'authPriv', snmpv3_auth_protocol: 'SHA', snmpv3_priv_protocol: 'AES', snmpv3_port: 161,
  https_enabled: false, https_auth_type: 'token', https_username: '', https_port: 443, https_verify_tls: true,
  netconf_enabled: false, netconf_port: 830, netconf_use_ssh_creds: true, netconf_username: '',
  gnmi_enabled: false, gnmi_username: '', gnmi_port: 57400, gnmi_tls_enabled: true,
}

const CONFIG_KEYS = Object.keys(DEFAULTS)

function CredentialModal({ profile, onClose, onSaved }: {
  profile: CredentialProfile | null
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = !!profile
  const [form, setForm] = useState<Form>(() => {
    if (!profile) return { ...DEFAULTS }
    const f: Form = { ...DEFAULTS }
    for (const k of CONFIG_KEYS) {
      const v = (profile as unknown as Record<string, unknown>)[k]
      if (v !== undefined && v !== null) f[k] = v as string | number | boolean
    }
    return f
  })
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const set = (k: string, v: string | number | boolean) => setForm((f) => ({ ...f, [k]: v }))
  const setSecret = (k: string, v: string) => setSecrets((s) => ({ ...s, [k]: v }))
  const enabled = (p: CredentialProtocol) => form[`${p}_enabled`] as boolean

  const submit = async () => {
    if (!String(form.name).trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    const payload: Record<string, unknown> = { ...form, name: String(form.name).trim() }
    // Only include secrets the user actually entered.
    for (const [k, v] of Object.entries(secrets)) if (v) payload[k] = v
    try {
      if (isEdit && profile) await updateCredential(profile.id, payload)
      else await createCredential(payload as { name: string })
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save profile.')
      setSaving(false)
    }
  }

  const secretPlaceholder = isEdit ? '•••••••• (unchanged)' : ''

  return (
    <Modal
      title={isEdit ? `Edit: ${profile!.name}` : 'New Credential Profile'}
      onClose={onClose}
      size="xl"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
            {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Profile'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}

        <Field label="Name">
          <input className={inputCls} value={String(form.name)} onChange={(e) => set('name', e.target.value)} placeholder="e.g. Cisco Standard" />
        </Field>
        <Field label="Description (optional)">
          <input className={inputCls} value={String(form.description)} onChange={(e) => set('description', e.target.value)} />
        </Field>

        <p className="text-xs text-gray-500 pt-1">{VAULT_NOTE}{isEdit && ' — leave secret fields blank to keep existing values.'}</p>

        {PROTOCOL_ORDER.map((p) => (
          <Section key={p} label={PROTOCOL_LABELS[p]} on={enabled(p)} onToggle={(v) => set(`${p}_enabled`, v)}>
            {p === 'ssh' && (
              <>
                <Row>
                  <Field label="Username"><input className={inputCls} value={String(form.ssh_username)} onChange={(e) => set('ssh_username', e.target.value)} /></Field>
                  <Field label="Auth method">
                    <select className={inputCls} value={String(form.ssh_auth_method)} onChange={(e) => set('ssh_auth_method', e.target.value)}>
                      <option value="password">Password</option>
                      <option value="key">SSH Key</option>
                    </select>
                  </Field>
                  <Field label="Port"><input type="number" className={inputCls} value={Number(form.ssh_port)} onChange={(e) => set('ssh_port', Number(e.target.value))} /></Field>
                </Row>
                {form.ssh_auth_method === 'password' ? (
                  <Field label="Password"><Secret value={secrets.ssh_password} onChange={(v) => setSecret('ssh_password', v)} placeholder={secretPlaceholder} /></Field>
                ) : (
                  <Row>
                    <Field label="Private key"><textarea className={`${inputCls} font-mono text-xs h-24`} value={secrets.ssh_private_key ?? ''} onChange={(e) => setSecret('ssh_private_key', e.target.value)} placeholder={isEdit ? '•••• (unchanged)' : '-----BEGIN OPENSSH PRIVATE KEY-----'} /></Field>
                    <Field label="Key passphrase (optional)"><Secret value={secrets.ssh_passphrase} onChange={(v) => setSecret('ssh_passphrase', v)} placeholder={secretPlaceholder} /></Field>
                  </Row>
                )}
              </>
            )}

            {p === 'snmpv3' && (
              <>
                <Row>
                  <Field label="Username"><input className={inputCls} value={String(form.snmpv3_username)} onChange={(e) => set('snmpv3_username', e.target.value)} /></Field>
                  <Field label="Security level">
                    <select className={inputCls} value={String(form.snmpv3_security_level)} onChange={(e) => set('snmpv3_security_level', e.target.value)}>
                      <option value="noAuthNoPriv">noAuthNoPriv</option>
                      <option value="authNoPriv">authNoPriv</option>
                      <option value="authPriv">authPriv</option>
                    </select>
                  </Field>
                  <Field label="Port"><input type="number" className={inputCls} value={Number(form.snmpv3_port)} onChange={(e) => set('snmpv3_port', Number(e.target.value))} /></Field>
                </Row>
                <Row>
                  <Field label="Auth protocol">
                    <select className={inputCls} value={String(form.snmpv3_auth_protocol)} onChange={(e) => set('snmpv3_auth_protocol', e.target.value)}>
                      {['SHA', 'SHA256', 'SHA512', 'MD5'].map((x) => <option key={x}>{x}</option>)}
                    </select>
                  </Field>
                  <Field label="Auth key"><Secret value={secrets.snmpv3_auth_key} onChange={(v) => setSecret('snmpv3_auth_key', v)} placeholder={secretPlaceholder} /></Field>
                </Row>
                <Row>
                  <Field label="Priv protocol">
                    <select className={inputCls} value={String(form.snmpv3_priv_protocol)} onChange={(e) => set('snmpv3_priv_protocol', e.target.value)}>
                      {['AES', 'AES192', 'AES256', 'DES'].map((x) => <option key={x}>{x}</option>)}
                    </select>
                  </Field>
                  <Field label="Priv key"><Secret value={secrets.snmpv3_priv_key} onChange={(v) => setSecret('snmpv3_priv_key', v)} placeholder={secretPlaceholder} /></Field>
                </Row>
              </>
            )}

            {p === 'snmpv2c' && (
              <Row>
                <Field label="Community string"><Secret value={secrets.snmpv2c_community} onChange={(v) => setSecret('snmpv2c_community', v)} placeholder={secretPlaceholder} /></Field>
                <Field label="Port"><input type="number" className={inputCls} value={Number(form.snmpv2c_port)} onChange={(e) => set('snmpv2c_port', Number(e.target.value))} /></Field>
              </Row>
            )}

            {p === 'https' && (
              <>
                <Row>
                  <Field label="Auth type">
                    <select className={inputCls} value={String(form.https_auth_type)} onChange={(e) => set('https_auth_type', e.target.value)}>
                      <option value="basic">Basic</option>
                      <option value="token">Bearer Token</option>
                      <option value="apikey">API Key</option>
                    </select>
                  </Field>
                  <Field label="Port"><input type="number" className={inputCls} value={Number(form.https_port)} onChange={(e) => set('https_port', Number(e.target.value))} /></Field>
                </Row>
                {form.https_auth_type === 'basic' && (
                  <Row>
                    <Field label="Username"><input className={inputCls} value={String(form.https_username)} onChange={(e) => set('https_username', e.target.value)} /></Field>
                    <Field label="Password"><Secret value={secrets.https_password} onChange={(v) => setSecret('https_password', v)} placeholder={secretPlaceholder} /></Field>
                  </Row>
                )}
                {form.https_auth_type === 'token' && (
                  <Field label="Bearer token"><Secret value={secrets.https_token} onChange={(v) => setSecret('https_token', v)} placeholder={secretPlaceholder} /></Field>
                )}
                {form.https_auth_type === 'apikey' && (
                  <Field label="API key"><Secret value={secrets.https_api_key} onChange={(v) => setSecret('https_api_key', v)} placeholder={secretPlaceholder} /></Field>
                )}
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={form.https_verify_tls as boolean} onChange={(e) => set('https_verify_tls', e.target.checked)} /> Verify TLS certificate
                </label>
              </>
            )}

            {p === 'netconf' && (
              <>
                <Row>
                  <Field label="Port"><input type="number" className={inputCls} value={Number(form.netconf_port)} onChange={(e) => set('netconf_port', Number(e.target.value))} /></Field>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 text-sm text-gray-700 py-2">
                      <input type="checkbox" checked={form.netconf_use_ssh_creds as boolean} onChange={(e) => set('netconf_use_ssh_creds', e.target.checked)} /> Use SSH credentials
                    </label>
                  </div>
                </Row>
                {!form.netconf_use_ssh_creds && (
                  <Field label="NETCONF username"><input className={inputCls} value={String(form.netconf_username)} onChange={(e) => set('netconf_username', e.target.value)} /></Field>
                )}
              </>
            )}

            {p === 'gnmi' && (
              <>
                <Row>
                  <Field label="Username"><input className={inputCls} value={String(form.gnmi_username)} onChange={(e) => set('gnmi_username', e.target.value)} /></Field>
                  <Field label="Port"><input type="number" className={inputCls} value={Number(form.gnmi_port)} onChange={(e) => set('gnmi_port', Number(e.target.value))} /></Field>
                </Row>
                <Field label="Password"><Secret value={secrets.gnmi_password} onChange={(v) => setSecret('gnmi_password', v)} placeholder={secretPlaceholder} /></Field>
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={form.gnmi_tls_enabled as boolean} onChange={(e) => set('gnmi_tls_enabled', e.target.checked)} /> TLS enabled
                </label>
                {form.gnmi_tls_enabled && (
                  <Row>
                    <Field label="Client cert (optional)"><textarea className={`${inputCls} font-mono text-xs h-20`} value={secrets.gnmi_client_cert ?? ''} onChange={(e) => setSecret('gnmi_client_cert', e.target.value)} placeholder={isEdit ? '•••• (unchanged)' : ''} /></Field>
                    <Field label="Client key (optional)"><textarea className={`${inputCls} font-mono text-xs h-20`} value={secrets.gnmi_client_key ?? ''} onChange={(e) => setSecret('gnmi_client_key', e.target.value)} placeholder={isEdit ? '•••• (unchanged)' : ''} /></Field>
                  </Row>
                )}
              </>
            )}
          </Section>
        ))}
      </div>
    </Modal>
  )
}

function Section({ label, on, onToggle, children }: {
  label: string; on: boolean; onToggle: (v: boolean) => void; children: React.ReactNode
}) {
  return (
    <div className={clsx('border rounded-lg transition-colors', on ? 'border-blue-200' : 'border-gray-200')}>
      <label className={clsx('flex items-center gap-2 px-3 py-2.5 cursor-pointer', on && 'border-b border-gray-100')}>
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} />
        <span className="text-sm font-medium text-gray-800">{label}</span>
        {on && <span className="ml-auto text-xs text-blue-600">enabled</span>}
      </label>
      {on && <div className="px-3 py-3 space-y-3">{children}</div>}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex-1 min-w-0"><label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>{children}</div>
}
function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col sm:flex-row gap-3">{children}</div>
}
function Secret({ value, onChange, placeholder }: { value?: string; onChange: (v: string) => void; placeholder?: string }) {
  return <input type="password" autoComplete="new-password" className={inputCls} value={value ?? ''} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
}

// ── Test modal ───────────────────────────────────────────────────────────────

function TestModal({ profile, onClose }: { profile: CredentialProfileListItem; onClose: () => void }) {
  const [ip, setIp] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<CredentialTestResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    if (!ip.trim()) { setErr('Enter an IP address to test against.'); return }
    setRunning(true); setErr(null); setResult(null)
    try { setResult(await testCredential(profile.id, ip.trim())) }
    catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErr(detail ?? 'Test request failed.')
    } finally { setRunning(false) }
  }

  return (
    <Modal
      title={`Test All — ${profile.name}`}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Close</button>
          <button onClick={run} disabled={running} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{running ? 'Testing…' : 'Run Test'}</button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Target IP address"><input className={inputCls} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.0.1" /></Field>
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
        {result && (
          <div className="space-y-2">
            {result.results.map((r) => (
              <div key={r.protocol} className={clsx('flex items-center gap-2 rounded-md px-3 py-2 text-sm border', r.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
                <span>{r.success ? '✅' : '❌'}</span>
                <span className="font-medium">{r.label}</span>
                <span className="text-xs opacity-80 truncate">{r.message}</span>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-gray-400">Probes service reachability per enabled protocol. Full auth is verified by the poller.</p>
      </div>
    </Modal>
  )
}

// ── Delete modal ─────────────────────────────────────────────────────────────

function DeleteModal({ profile, onClose, onDeleted }: {
  profile: CredentialProfileListItem; onClose: () => void; onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const confirm = async () => {
    setBusy(true); setErr(null)
    try { await deleteCredential(profile.id); onDeleted() }
    catch { setErr('Failed to delete profile.'); setBusy(false) }
  }
  return (
    <Modal
      title="Delete credential profile?"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={confirm} disabled={busy} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      <p className="text-sm text-gray-700"><span className="font-medium">{profile.name}</span> will be removed and its secrets deleted from OpenBao.</p>
      {profile.device_count > 0 && (
        <div className="mt-3 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2 text-sm text-yellow-800">
          ⚠ Assigned to <span className="font-medium">{profile.device_count} device{profile.device_count !== 1 ? 's' : ''}</span>. They'll be left without credentials — reassign first.
        </div>
      )}
      {err && <div className="mt-3 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
    </Modal>
  )
}

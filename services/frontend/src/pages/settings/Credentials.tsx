import { useCallback, useEffect, useState } from 'react'
import {
  fetchCredentials, fetchCredential, createCredential, updateCredential,
  deleteCredential, testCredential,
  type CredentialProfileListItem, type CredentialProfile,
  type CredentialProfilePayload, type CredentialType, type CredentialTestResult,
} from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'

// ── Credential type metadata ─────────────────────────────────────────────────

type SecretField = { key: keyof CredentialProfilePayload; label: string; multiline?: boolean }

interface TypeMeta {
  label: string
  category: string
  showUsername: boolean
  showPort: boolean
  showTls: boolean
  isSnmpV3: boolean
  secrets: SecretField[]
}

const TYPE_META: Record<CredentialType, TypeMeta> = {
  snmpv1:       { label: 'SNMP v1',        category: 'SNMP',         showUsername: false, showPort: true,  showTls: false, isSnmpV3: false, secrets: [{ key: 'community', label: 'Community String' }] },
  snmpv2c:      { label: 'SNMP v2c',       category: 'SNMP',         showUsername: false, showPort: true,  showTls: false, isSnmpV3: false, secrets: [{ key: 'community', label: 'Community String' }] },
  snmpv3:       { label: 'SNMP v3',        category: 'SNMP',         showUsername: true,  showPort: true,  showTls: false, isSnmpV3: true,  secrets: [{ key: 'auth_password', label: 'Auth Password' }, { key: 'priv_password', label: 'Priv Password' }] },
  ssh_password: { label: 'SSH (password)', category: 'SSH',          showUsername: true,  showPort: true,  showTls: false, isSnmpV3: false, secrets: [{ key: 'password', label: 'Password' }] },
  ssh_key:      { label: 'SSH (key)',      category: 'SSH',          showUsername: true,  showPort: true,  showTls: false, isSnmpV3: false, secrets: [{ key: 'private_key', label: 'Private Key', multiline: true }, { key: 'passphrase', label: 'Key Passphrase (optional)' }] },
  http_basic:   { label: 'HTTP Basic',     category: 'HTTP / API',   showUsername: true,  showPort: true,  showTls: true,  isSnmpV3: false, secrets: [{ key: 'password', label: 'Password' }] },
  http_token:   { label: 'HTTP Token',     category: 'HTTP / API',   showUsername: false, showPort: true,  showTls: true,  isSnmpV3: false, secrets: [{ key: 'token', label: 'Bearer Token' }] },
  http_apikey:  { label: 'HTTP API Key',   category: 'HTTP / API',   showUsername: false, showPort: true,  showTls: true,  isSnmpV3: false, secrets: [{ key: 'api_key', label: 'API Key' }] },
  gnmi:         { label: 'gNMI',           category: 'Streaming',    showUsername: true,  showPort: true,  showTls: true,  isSnmpV3: false, secrets: [{ key: 'password', label: 'Password' }] },
  netconf:      { label: 'NETCONF',        category: 'Streaming',    showUsername: true,  showPort: true,  showTls: false, isSnmpV3: false, secrets: [{ key: 'password', label: 'Password' }] },
}

const CATEGORIES = ['SNMP', 'SSH', 'HTTP / API', 'Streaming']
const TYPES_IN_CATEGORY = (cat: string) =>
  (Object.keys(TYPE_META) as CredentialType[]).filter((t) => TYPE_META[t].category === cat)

const VAULT_NOTE = '🔒 Stored securely in OpenBao'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

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
      .then((data) => { setItems(data); setError(null) })
      .catch(() => setError('Failed to load credential profiles. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const openEdit = async (id: number) => {
    try {
      setEditing(await fetchCredential(id))
    } catch {
      setError('Failed to load credential profile.')
    }
  }

  const grouped = CATEGORIES.map((cat) => ({
    cat,
    rows: items.filter((i) => TYPE_META[i.credential_type]?.category === cat),
  })).filter((g) => g.rows.length > 0)

  return (
    <div>
      <SectionHeader
        title="Credentials"
        description="Reusable authentication profiles. Secrets are stored in OpenBao — never in the database."
        action={
          <button
            onClick={() => setEditing('new')}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
          >
            + New Profile
          </button>
        }
      />

      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 mb-4">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200">
          <EmptyState
            title="No credential profiles yet"
            description="Create a profile for SNMP, SSH, HTTP, gNMI or NETCONF. Secrets are written straight to OpenBao."
            action={{ label: 'New Profile', onClick: () => setEditing('new') }}
            icon="🔑"
          />
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(({ cat, rows }) => (
            <section key={cat}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">{cat}</h3>
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden divide-y divide-gray-100">
                {rows.map((row) => (
                  <CredentialRow
                    key={row.id}
                    row={row}
                    onEdit={() => openEdit(row.id)}
                    onTest={() => setTesting(row)}
                    onDelete={() => setDeleting(row)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {editing && (
        <CredentialModal
          profile={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}
      {testing && (
        <TestModal profile={testing} onClose={() => setTesting(null)} />
      )}
      {deleting && (
        <DeleteModal
          profile={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

// ── Row ──────────────────────────────────────────────────────────────────────

function CredentialRow({ row, onEdit, onTest, onDelete }: {
  row: CredentialProfileListItem
  onEdit: () => void
  onTest: () => void
  onDelete: () => void
}) {
  const meta = TYPE_META[row.credential_type]
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-gray-900 truncate">{row.name}</p>
        <p className="text-xs text-gray-500">
          {meta?.label ?? row.credential_type}
          {row.username && <> · <span className="font-mono">{row.username}</span></>}
        </p>
      </div>
      <span className="text-xs text-gray-500 whitespace-nowrap">
        {row.device_count} device{row.device_count !== 1 ? 's' : ''}
      </span>
      <TestBadge result={row.last_test_result} />
      <div className="flex items-center gap-2">
        <button onClick={onTest} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Test</button>
        <button onClick={onEdit} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Edit</button>
        <button onClick={onDelete} className="px-2.5 py-1 text-xs border border-red-200 text-red-600 rounded-md hover:bg-red-50">Delete</button>
      </div>
    </div>
  )
}

function TestBadge({ result }: { result: string }) {
  const map: Record<string, string> = {
    success: 'bg-green-100 text-green-700',
    failure: 'bg-red-100 text-red-700',
    untested: 'bg-gray-100 text-gray-500',
  }
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${map[result] ?? map.untested}`}>
      {result === 'untested' ? 'Untested' : result === 'success' ? 'Tested OK' : 'Test failed'}
    </span>
  )
}

// ── Create / Edit modal ──────────────────────────────────────────────────────

function CredentialModal({ profile, onClose, onSaved }: {
  profile: CredentialProfile | null
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = !!profile
  const [type, setType] = useState<CredentialType>(profile?.credential_type ?? 'snmpv2c')
  const [form, setForm] = useState<Record<string, string>>({
    name: profile?.name ?? '',
    description: profile?.description ?? '',
    username: profile?.username ?? '',
    port: profile?.port != null ? String(profile.port) : '',
    snmp_security_level: profile?.snmp_security_level ?? 'authPriv',
    auth_protocol: profile?.auth_protocol ?? 'SHA',
    priv_protocol: profile?.priv_protocol ?? 'AES',
  })
  const [tls, setTls] = useState<boolean>(profile?.tls_enabled ?? false)
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const meta = TYPE_META[type]
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const submit = async () => {
    if (!form.name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    const payload: CredentialProfilePayload = {
      name: form.name.trim(),
      credential_type: type,
      description: form.description,
      username: meta.showUsername ? form.username : '',
      port: form.port ? Number(form.port) : null,
      tls_enabled: meta.showTls ? tls : false,
      snmp_version: type === 'snmpv1' ? '1' : type === 'snmpv2c' ? '2c' : type === 'snmpv3' ? '3' : '',
      snmp_security_level: meta.isSnmpV3 ? form.snmp_security_level : '',
      auth_protocol: meta.isSnmpV3 ? form.auth_protocol : '',
      priv_protocol: meta.isSnmpV3 ? form.priv_protocol : '',
    }
    // Only send secret fields that were actually entered.
    for (const s of meta.secrets) {
      const v = secrets[s.key as string]
      if (v) (payload as unknown as Record<string, unknown>)[s.key as string] = v
    }
    try {
      if (isEdit && profile) await updateCredential(profile.id, payload)
      else await createCredential(payload)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save profile.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={isEdit ? `Edit: ${profile!.name}` : 'New Credential Profile'}
      onClose={onClose}
      size="lg"
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
          <input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="e.g. DC Core SNMPv3" />
        </Field>

        <Field label="Credential Type">
          <select
            className={inputCls}
            value={type}
            disabled={isEdit}
            onChange={(e) => { setType(e.target.value as CredentialType); setSecrets({}) }}
          >
            {CATEGORIES.map((cat) => (
              <optgroup key={cat} label={cat}>
                {TYPES_IN_CATEGORY(cat).map((t) => (
                  <option key={t} value={t}>{TYPE_META[t].label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {isEdit && <p className="text-xs text-gray-400 mt-1">Type can't be changed after creation.</p>}
        </Field>

        {meta.showUsername && (
          <Field label="Username">
            <input className={inputCls} value={form.username} onChange={(e) => set('username', e.target.value)} />
          </Field>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Port (optional — defaults per protocol)">
            <input className={inputCls} type="number" value={form.port} onChange={(e) => set('port', e.target.value)} placeholder="auto" />
          </Field>
          {meta.showTls && (
            <Field label="TLS">
              <label className="flex items-center gap-2 text-sm text-gray-700 py-2">
                <input type="checkbox" checked={tls} onChange={(e) => setTls(e.target.checked)} />
                Enable TLS
              </label>
            </Field>
          )}
        </div>

        {meta.isSnmpV3 && (
          <div className="grid grid-cols-3 gap-3">
            <Field label="Security Level">
              <select className={inputCls} value={form.snmp_security_level} onChange={(e) => set('snmp_security_level', e.target.value)}>
                <option value="noAuthNoPriv">noAuthNoPriv</option>
                <option value="authNoPriv">authNoPriv</option>
                <option value="authPriv">authPriv</option>
              </select>
            </Field>
            <Field label="Auth Protocol">
              <select className={inputCls} value={form.auth_protocol} onChange={(e) => set('auth_protocol', e.target.value)}>
                {['SHA', 'SHA256', 'SHA512', 'MD5'].map((p) => <option key={p}>{p}</option>)}
              </select>
            </Field>
            <Field label="Priv Protocol">
              <select className={inputCls} value={form.priv_protocol} onChange={(e) => set('priv_protocol', e.target.value)}>
                {['AES', 'AES192', 'AES256', 'DES'].map((p) => <option key={p}>{p}</option>)}
              </select>
            </Field>
          </div>
        )}

        {/* Secrets */}
        <div className="border-t border-gray-100 pt-4 space-y-3">
          <p className="text-xs text-gray-500">{VAULT_NOTE}{isEdit && ' — leave blank to keep the existing value.'}</p>
          {meta.secrets.map((s) => (
            <Field key={s.key as string} label={s.label}>
              {s.multiline ? (
                <textarea
                  className={`${inputCls} font-mono text-xs h-28`}
                  value={secrets[s.key as string] ?? ''}
                  onChange={(e) => setSecrets((p) => ({ ...p, [s.key as string]: e.target.value }))}
                  placeholder={isEdit ? '•••••••• (unchanged)' : '-----BEGIN OPENSSH PRIVATE KEY-----'}
                />
              ) : (
                <input
                  type="password"
                  autoComplete="new-password"
                  className={inputCls}
                  value={secrets[s.key as string] ?? ''}
                  onChange={(e) => setSecrets((p) => ({ ...p, [s.key as string]: e.target.value }))}
                  placeholder={isEdit ? '•••••••• (unchanged)' : ''}
                />
              )}
            </Field>
          ))}
        </div>

        <Field label="Description (optional)">
          <input className={inputCls} value={form.description} onChange={(e) => set('description', e.target.value)} />
        </Field>
      </div>
    </Modal>
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

// ── Test modal ───────────────────────────────────────────────────────────────

function TestModal({ profile, onClose }: { profile: CredentialProfileListItem; onClose: () => void }) {
  const [ip, setIp] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<CredentialTestResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    if (!ip.trim()) { setErr('Enter an IP address to test against.'); return }
    setRunning(true); setErr(null); setResult(null)
    try {
      setResult(await testCredential(profile.id, ip.trim()))
    } catch {
      setErr('Test request failed.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal
      title={`Test: ${profile.name}`}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Close</button>
          <button onClick={run} disabled={running} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
            {running ? 'Testing…' : 'Run Test'}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Target IP address">
          <input className={inputCls} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.0.1" />
        </Field>
        {err && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
        {result && (
          <div className={`rounded-lg px-3 py-3 text-sm border ${result.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800'}`}>
            <p className="font-medium">{result.success ? '✓ Reachable' : '✗ Failed'}{result.port ? ` (port ${result.port})` : ''}</p>
            <p className="mt-1">{result.message}</p>
            {result.latency_ms != null && <p className="mt-1 text-xs opacity-80">Latency: {result.latency_ms} ms</p>}
          </div>
        )}
        <p className="text-xs text-gray-400">
          This probes service reachability. Full protocol authentication is verified by the poller.
        </p>
      </div>
    </Modal>
  )
}

// ── Delete modal ─────────────────────────────────────────────────────────────

function DeleteModal({ profile, onClose, onDeleted }: {
  profile: CredentialProfileListItem
  onClose: () => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const confirm = async () => {
    setBusy(true); setErr(null)
    try {
      await deleteCredential(profile.id)
      onDeleted()
    } catch {
      setErr('Failed to delete profile.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Delete credential profile?"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={confirm} disabled={busy} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </>
      }
    >
      <p className="text-sm text-gray-700">
        <span className="font-medium">{profile.name}</span> will be removed and its secret deleted from OpenBao.
      </p>
      {profile.device_count > 0 && (
        <div className="mt-3 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2 text-sm text-yellow-800">
          ⚠ This profile is assigned to <span className="font-medium">{profile.device_count} device{profile.device_count !== 1 ? 's' : ''}</span>.
          Those associations will be removed — reassign them first to avoid leaving devices without credentials.
        </div>
      )}
      {err && <div className="mt-3 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{err}</div>}
    </Modal>
  )
}

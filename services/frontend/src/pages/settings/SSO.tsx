import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchSSOProviders, createSSOProvider, updateSSOProvider, deleteSSOProvider,
  testSSOProvider,
  type SSOProvider, type SSOProviderInput, type SSOTestResult,
} from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const VAULT_NOTE = '🔒 Stored securely in OpenBao'

const PROVIDER_TYPES: { value: string; label: string }[] = [
  { value: 'google-oauth2', label: 'Google Workspace' },
  { value: 'azuread-tenant-oauth2', label: 'Microsoft Azure AD' },
  { value: 'okta-oauth2', label: 'Okta' },
  { value: 'github', label: 'GitHub' },
]
const ROLES = ['viewer', 'engineer', 'admin', 'api']
const providerLabel = (p: string) => PROVIDER_TYPES.find((t) => t.value === p)?.label ?? p

interface FormState {
  name: string
  provider: string
  client_id: string
  client_secret: string
  tenant_id: string
  okta_domain: string
  allowed_domains: string
  allow_signup: boolean
  default_role: string
  is_default: boolean
  is_enabled: boolean
}

function blankForm(): FormState {
  return {
    name: '', provider: 'google-oauth2', client_id: '', client_secret: '',
    tenant_id: '', okta_domain: '', allowed_domains: '', allow_signup: true,
    default_role: 'viewer', is_default: false, is_enabled: true,
  }
}

function formFromProvider(p: SSOProvider): FormState {
  return {
    name: p.name, provider: p.provider, client_id: p.client_id, client_secret: '',
    tenant_id: p.tenant_id, okta_domain: p.okta_domain,
    allowed_domains: (p.allowed_domains ?? []).join(', '),
    allow_signup: p.allow_signup, default_role: p.default_role,
    is_default: p.is_default, is_enabled: p.is_enabled,
  }
}

export default function SSO() {
  const [items, setItems] = useState<SSOProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<SSOProvider | 'new' | null>(null)
  const [deleting, setDeleting] = useState<SSOProvider | null>(null)
  const [testResults, setTestResults] = useState<Record<number, SSOTestResult | 'pending'>>({})

  const load = useCallback(() => {
    setLoading(true)
    fetchSSOProviders()
      .then((d) => { setItems(d); setError(null) })
      .catch(() => setError('Failed to load SSO providers. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const toggleEnabled = async (p: SSOProvider) => {
    await updateSSOProvider(p.id, { is_enabled: !p.is_enabled })
    load()
  }

  const runTest = async (p: SSOProvider) => {
    setTestResults((r) => ({ ...r, [p.id]: 'pending' }))
    try {
      const res = await testSSOProvider(p.id)
      setTestResults((r) => ({ ...r, [p.id]: res }))
    } catch {
      setTestResults((r) => ({ ...r, [p.id]: { valid: false, error: 'Test request failed' } }))
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    await deleteSSOProvider(deleting.id)
    setDeleting(null)
    load()
  }

  return (
    <div>
      <SectionHeader
        title="SSO Providers"
        description="Single sign-on via external identity providers. Local login always remains available as a fallback."
        action={
          <button
            onClick={() => setEditing('new')}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
          >
            + Add Provider
          </button>
        }
      />

      {error && (
        <div className="mb-4 px-4 py-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="🔑"
          title="No SSO providers configured"
          description="Add a provider (Google, Microsoft Azure AD, Okta, or GitHub) to let users sign in with their organization account."
          action={{ label: '+ Add Provider', onClick: () => setEditing('new') }}
        />
      ) : (
        <div className="space-y-3">
          {items.map((p) => {
            const result = testResults[p.id]
            return (
              <div
                key={p.id}
                className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 bg-white dark:bg-gray-900"
              >
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-gray-900 dark:text-gray-100">{p.name}</span>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
                        {providerLabel(p.provider)}
                      </span>
                      {p.is_default && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-950 text-blue-700 dark:text-blue-300">
                          default
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      {p.allowed_domains.length > 0
                        ? `Allowed domains: ${p.allowed_domains.join(', ')}`
                        : 'All email domains allowed'}
                      {' · '}new users → {p.default_role}
                      {!p.has_secret && ' · ⚠ no client secret'}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {/* Enabled toggle */}
                    <button
                      onClick={() => toggleEnabled(p)}
                      title={p.is_enabled ? 'Disable' : 'Enable'}
                      className={clsx(
                        'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                        p.is_enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600',
                      )}
                    >
                      <span
                        className={clsx(
                          'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
                          p.is_enabled ? 'translate-x-6' : 'translate-x-1',
                        )}
                      />
                    </button>
                    <button onClick={() => runTest(p)} className="text-sm px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200">
                      Test
                    </button>
                    <button onClick={() => setEditing(p)} className="text-sm px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200">
                      Edit
                    </button>
                    <button onClick={() => setDeleting(p)} className="text-sm px-3 py-1.5 border border-red-300 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-950">
                      Delete
                    </button>
                  </div>
                </div>

                {result && result !== 'pending' && (
                  <div className={clsx(
                    'mt-3 text-xs px-3 py-2 rounded-lg',
                    result.valid
                      ? 'bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300'
                      : 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300',
                  )}>
                    {result.valid ? '✓ Configuration valid' : `⚠ ${result.error}`}
                  </div>
                )}
                {result === 'pending' && (
                  <div className="mt-3 text-xs text-gray-500">Testing…</div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {editing && (
        <ProviderForm
          initial={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}

      {deleting && (
        <Modal
          title="Delete SSO provider"
          onClose={() => setDeleting(null)}
          footer={
            <>
              <button onClick={() => setDeleting(null)} className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg">Cancel</button>
              <button onClick={confirmDelete} className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg">Delete</button>
            </>
          }
        >
          <p className="text-sm text-gray-600 dark:text-gray-300">
            Delete <span className="font-semibold">{deleting.name}</span>? Users will no longer be
            able to sign in with this provider. Local login is unaffected.
          </p>
        </Modal>
      )}
    </div>
  )
}

function ProviderForm({ initial, onClose, onSaved }: {
  initial: SSOProvider | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<FormState>(initial ? formFromProvider(initial) : blankForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }))

  const isAzure = form.provider === 'azuread-tenant-oauth2'
  const isOkta = form.provider === 'okta-oauth2'

  const submit = async () => {
    if (!form.name.trim()) { setError('Name is required.'); return }
    setSaving(true)
    setError(null)
    const payload: SSOProviderInput = {
      name: form.name.trim(),
      provider: form.provider,
      client_id: form.client_id.trim(),
      tenant_id: isAzure ? form.tenant_id.trim() : '',
      okta_domain: isOkta ? form.okta_domain.trim() : '',
      allowed_domains: form.allowed_domains.split(/[,\s]+/).map((d) => d.trim()).filter(Boolean),
      allow_signup: form.allow_signup,
      default_role: form.default_role,
      is_default: form.is_default,
      is_enabled: form.is_enabled,
    }
    // Only send the secret when the admin actually entered one (keeps the
    // existing OpenBao value on edit).
    if (form.client_secret) payload.client_secret = form.client_secret
    try {
      if (initial) await updateSSOProvider(initial.id, payload)
      else await createSSOProvider(payload)
      onSaved()
    } catch {
      setError('Failed to save provider. Check the fields and try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={initial ? `Edit ${initial.name}` : 'Add SSO provider'}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg">Cancel</button>
          <button onClick={submit} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg">
            {saving ? 'Saving…' : 'Save & Enable'}
          </button>
        </>
      }
    >
      {error && (
        <div className="mb-4 px-4 py-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Provider type</label>
          <select className={inputCls} value={form.provider} onChange={(e) => set('provider', e.target.value)} disabled={!!initial}>
            {PROVIDER_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Display name</label>
          <input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Company Google" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Client ID</label>
          <input className={inputCls} value={form.client_id} onChange={(e) => set('client_id', e.target.value)} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Client secret</label>
          <input
            type="password" className={inputCls} value={form.client_secret}
            onChange={(e) => set('client_secret', e.target.value)}
            placeholder={initial?.has_secret ? '•••••••• (leave blank to keep current)' : ''}
          />
          <p className="text-xs text-gray-500 mt-1">{VAULT_NOTE}</p>
        </div>

        {isAzure && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Tenant ID</label>
            <input className={inputCls} value={form.tenant_id} onChange={(e) => set('tenant_id', e.target.value)} placeholder="your-tenant-id" />
          </div>
        )}
        {isOkta && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Okta domain</label>
            <input className={inputCls} value={form.okta_domain} onChange={(e) => set('okta_domain', e.target.value)} placeholder="company.okta.com" />
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Allowed email domains</label>
          <input className={inputCls} value={form.allowed_domains} onChange={(e) => set('allowed_domains', e.target.value)} placeholder="company.com (optional, comma-separated; blank = any)" />
        </div>

        <div className="flex items-center gap-2">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">Default role for new users</label>
          <select className={clsx(inputCls, 'w-auto')} value={form.default_role} onChange={(e) => set('default_role', e.target.value)}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={form.allow_signup} onChange={(e) => set('allow_signup', e.target.checked)} />
          Allow new user signup via SSO
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={form.is_default} onChange={(e) => set('is_default', e.target.checked)} />
          Set as default provider (auto-redirect on the login page)
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={form.is_enabled} onChange={(e) => set('is_enabled', e.target.checked)} />
          Enabled
        </label>
      </div>
    </Modal>
  )
}

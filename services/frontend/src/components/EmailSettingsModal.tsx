import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchEmailSettings, saveEmailSettings, sendTestEmail,
  type EmailSettings, type EmailProviderPreset,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const PROVIDERS: { id: string; name: string }[] = [
  { id: 'gmail', name: 'Gmail' },
  { id: 'm365', name: 'Microsoft 365' },
  { id: 'sendgrid', name: 'SendGrid' },
  { id: 'mailgun', name: 'Mailgun' },
  { id: 'custom', name: 'Custom SMTP' },
]

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'

export default function EmailSettingsModal({ onClose, onSaved }: { onClose: () => void; onSaved?: () => void }) {
  const [cfg, setCfg] = useState<EmailSettings | null>(null)
  const [presets, setPresets] = useState<Record<string, EmailProviderPreset>>({})
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [testTo, setTestTo] = useState('')

  useEffect(() => {
    fetchEmailSettings()
      .then((s) => { setCfg(s); setPresets(s.provider_presets || {}); setTestTo(s.from_email || '') })
      .catch((e) => setError(parseApiErrors(e, 'Failed to load email settings.')))
  }, [])

  if (!cfg) {
    return <Modal title="Email / SMTP" onClose={onClose}><div className="py-8 text-center text-sm text-gray-400">{error || 'Loading…'}</div></Modal>
  }

  const set = (patch: Partial<EmailSettings>) => setCfg({ ...cfg, ...patch })

  // Selecting a provider auto-fills host/port/TLS (and username for SendGrid).
  const pickProvider = (provider: string) => {
    const p = presets[provider]
    set({
      provider,
      ...(p ? { host: p.host, port: p.port, use_tls: p.use_tls, use_ssl: p.use_ssl,
                username: p.username ?? cfg.username } : {}),
    })
  }

  const help = presets[cfg.provider]?.help

  const save = async () => {
    setBusy(true); setError(null); setMsg(null)
    try {
      await saveEmailSettings({ ...cfg, ...(password ? { password } : {}) })
      setPassword(''); setMsg('Saved.'); onSaved?.()
    } catch (e) { setError(parseApiErrors(e, 'Failed to save email settings.')) }
    finally { setBusy(false) }
  }

  const test = async () => {
    if (!testTo) { setError('Enter a recipient for the test email.'); return }
    setBusy(true); setError(null); setMsg(null)
    try {
      // Persist current settings first so the test uses them.
      await saveEmailSettings({ ...cfg, ...(password ? { password } : {}) })
      setPassword('')
      const r = await sendTestEmail(testTo)
      if (r.sent) setMsg(`✅ Test email sent to ${testTo}`)
      else setError(`❌ Failed: ${r.error || 'unknown error'}`)
    } catch (e) { setError(parseApiErrors(e, 'Failed to send test email.')) }
    finally { setBusy(false) }
  }

  return (
    <Modal
      title="Email / SMTP"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>
          <button onClick={save} disabled={busy} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
        {msg && <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-900 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-300">{msg}</div>}

        <div>
          <label className={label}>Email Provider</label>
          <div className="grid grid-cols-2 gap-2">
            {PROVIDERS.map((p) => (
              <button key={p.id} type="button" onClick={() => pickProvider(p.id)}
                className={`px-3 py-1.5 text-sm rounded-lg border ${cfg.provider === p.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50 text-gray-700 dark:text-gray-300'}`}>
                {p.name}
              </button>
            ))}
          </div>
          {help && <p className="mt-2 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 rounded-lg px-3 py-2">{help}</p>}
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2"><label className={label}>SMTP Host</label><input className={input} value={cfg.host} onChange={(e) => set({ host: e.target.value })} placeholder="smtp.example.com" /></div>
          <div><label className={label}>Port</label><input type="number" className={input} value={cfg.port} onChange={(e) => set({ port: Number(e.target.value) })} /></div>
        </div>
        <div><label className={label}>Username / Email</label><input className={input} value={cfg.username} onChange={(e) => set({ username: e.target.value })} autoComplete="off" /></div>
        <div>
          <label className={label}>Password {cfg.password_set && <span className="text-xs text-gray-400">(stored in OpenBao — leave blank to keep)</span>}</label>
          <input type="password" className={input} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={cfg.password_set ? '••••••••' : ''} autoComplete="new-password" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div><label className={label}>From Name</label><input className={input} value={cfg.from_name} onChange={(e) => set({ from_name: e.target.value })} /></div>
          <div><label className={label}>From Email</label><input className={input} value={cfg.from_email} onChange={(e) => set({ from_email: e.target.value })} placeholder="alerts@example.com" /></div>
        </div>
        <div className="flex items-center gap-6">
          <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" checked={cfg.use_tls} onChange={(e) => set({ use_tls: e.target.checked, use_ssl: e.target.checked ? false : cfg.use_ssl })} /> Use TLS</label>
          <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" checked={cfg.use_ssl} onChange={(e) => set({ use_ssl: e.target.checked, use_tls: e.target.checked ? false : cfg.use_tls })} /> Use SSL</label>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
        </div>

        <div className="border-t border-gray-100 dark:border-gray-700 pt-3">
          <label className={label}>Send a test email</label>
          <div className="flex gap-2">
            <input className={input} value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="you@example.com" />
            <button onClick={test} disabled={busy} className="shrink-0 px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg disabled:opacity-50 dark:text-gray-200">Send Test Email</button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

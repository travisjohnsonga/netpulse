import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import {
  fetchSSLStatus, generateSelfSigned, generateCSR, uploadCertificate,
  type SSLStatus,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const EXPIRY_BADGE: Record<string, string> = {
  ok: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  warning: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  critical: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  expired: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  not_yet_valid: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
  none: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
}

type Mode = 'self_signed' | 'csr' | 'upload'

export default function Certificates() {
  const [status, setStatus] = useState<SSLStatus | null>(null)
  const [mode, setMode] = useState<Mode>('self_signed')
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // form state
  const [cn, setCn] = useState('')
  const [sans, setSans] = useState('')
  const [days, setDays] = useState('825')
  const [org, setOrg] = useState('')
  const [country, setCountry] = useState('')
  const [csrOut, setCsrOut] = useState<string | null>(null)
  const [certPem, setCertPem] = useState('')
  const [keyPem, setKeyPem] = useState('')
  const [chainPem, setChainPem] = useState('')

  const load = () => fetchSSLStatus().then(setStatus).catch(() => setError('Failed to load certificate status.'))
  useEffect(() => { load() }, [])

  const sanList = () => sans.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean)

  const doSelfSigned = async () => {
    setBusy(true); setError(null); setMsg(null)
    try {
      const s = await generateSelfSigned({ common_name: cn, sans: sanList(), days: Number(days) })
      setStatus(s); setMsg('Self-signed certificate generated and installed. Restart the frontend container to serve it.')
    } catch { setError('Failed to generate self-signed certificate.') } finally { setBusy(false) }
  }

  const doCSR = async () => {
    setBusy(true); setError(null); setMsg(null); setCsrOut(null)
    try {
      const { csr } = await generateCSR({ common_name: cn, sans: sanList(), organization: org, country })
      setCsrOut(csr); setMsg('CSR generated. Send it to your CA, then upload the signed certificate below.')
      load()
    } catch { setError('Failed to generate CSR.') } finally { setBusy(false) }
  }

  const doUpload = async () => {
    setBusy(true); setError(null); setMsg(null)
    try {
      const s = await uploadCertificate({
        certificate: certPem,
        private_key: keyPem.trim() || undefined,
        chain: chainPem.trim() || undefined,
      })
      setStatus(s); setMsg('Certificate installed. Restart the frontend container to serve it.')
      setCertPem(''); setKeyPem(''); setChainPem('')
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail || 'Failed to upload certificate.')
    } finally { setBusy(false) }
  }

  return (
    <div>
      <SectionHeader
        title="Certificates"
        description="NetPulse's own HTTPS server certificate — the cert nginx serves the web UI and API with. This is not for network devices."
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4 max-w-3xl">{error}</div>}
      {msg && <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-400 mb-4 max-w-3xl">{msg}</div>}

      {/* Current status */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 mb-4 max-w-3xl">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Installed certificate</h3>
          {status && (
            <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', EXPIRY_BADGE[status.expiry_status])}>
              {!status.installed ? 'None installed'
                : status.expiry_status === 'ok' ? `Valid · ${status.days_remaining}d left`
                : status.expiry_status === 'expired' ? 'Expired'
                : status.expiry_status === 'not_yet_valid' ? 'Not yet valid'
                : `Expiring · ${status.days_remaining}d left`}
            </span>
          )}
        </div>
        {!status ? <p className="text-sm text-gray-400 dark:text-gray-500">Loading…</p>
          : !status.installed ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">No certificate installed yet. A temporary self-signed cert is used until you install one below.</p>
          ) : (
            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3 text-sm">
              <Info label="Common name" value={status.common_name || '—'} />
              <Info label="Issuer" value={status.issuer || '—'} />
              <Info label="Source" value={status.source || '—'} />
              <Info label="SANs" value={status.sans.join(', ') || '—'} />
              <Info label="Valid until" value={status.not_after ? new Date(status.not_after).toLocaleString() : '—'} />
              <Info label="Private key" value={status.has_private_key ? 'present ✅' : 'missing ⚠️'} />
              <Info label="Serial" value={status.serial || '—'} mono />
              <Info label="SHA-256" value={status.fingerprint_sha256 ? status.fingerprint_sha256.slice(0, 32) + '…' : '—'} mono />
            </dl>
          )}
        {status?.pending_csr && (
          <div className="mt-3 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-gray-700 rounded-lg px-3 py-2">
            A CSR is pending — upload the CA-signed certificate to complete installation.
          </div>
        )}
      </div>

      {/* Install / replace */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 max-w-3xl">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Install / replace certificate</h3>
        <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4">
          {([['self_signed', 'Self-signed'], ['csr', 'Generate CSR'], ['upload', 'Upload']] as [Mode, string][]).map(([m, label]) => (
            <button key={m} onClick={() => { setMode(m); setError(null); setMsg(null) }}
              className={clsx('px-4 py-2 text-sm font-medium border-b-2 -mb-px', mode === m ? 'border-blue-600 text-blue-700 dark:text-blue-400' : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-100')}>
              {label}
            </button>
          ))}
        </div>

        {mode === 'self_signed' && (
          <div className="space-y-4">
            <p className="text-xs text-gray-500 dark:text-gray-400">Quickest option — good for internal/lab use. Browsers will warn unless the cert is trusted manually.</p>
            <Field label="Common name (FQDN)"><input className={inputCls} value={cn} onChange={(e) => setCn(e.target.value)} placeholder="netpulse.example.com" /></Field>
            <Field label="Subject alternative names (comma/space separated)"><input className={inputCls} value={sans} onChange={(e) => setSans(e.target.value)} placeholder="netpulse.example.com, 10.0.0.5" /></Field>
            <Field label="Validity (days)"><input className={`${inputCls} max-w-[10rem]`} type="number" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
            <button onClick={doSelfSigned} disabled={busy || !cn} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {busy ? 'Generating…' : 'Generate & Install'}
            </button>
          </div>
        )}

        {mode === 'csr' && (
          <div className="space-y-4">
            <p className="text-xs text-gray-500 dark:text-gray-400">Generates a private key (kept on the server) and a CSR to send to your CA. Upload the signed cert under the Upload tab afterwards.</p>
            <Field label="Common name (FQDN)"><input className={inputCls} value={cn} onChange={(e) => setCn(e.target.value)} placeholder="netpulse.example.com" /></Field>
            <Field label="Subject alternative names"><input className={inputCls} value={sans} onChange={(e) => setSans(e.target.value)} placeholder="netpulse.example.com, www.example.com" /></Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Organization"><input className={inputCls} value={org} onChange={(e) => setOrg(e.target.value)} /></Field>
              <Field label="Country (2-letter)"><input className={inputCls} maxLength={2} value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} /></Field>
            </div>
            <button onClick={doCSR} disabled={busy || !cn} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {busy ? 'Generating…' : 'Generate CSR'}
            </button>
            {csrOut && (
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-gray-600 dark:text-gray-400">Certificate Signing Request</span>
                  <button onClick={() => navigator.clipboard.writeText(csrOut)} className="text-xs border border-gray-300 dark:border-gray-600 rounded-md px-2 py-1 hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">📋 Copy</button>
                </div>
                <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-md p-3 overflow-x-auto max-h-60 whitespace-pre-wrap">{csrOut}</pre>
              </div>
            )}
          </div>
        )}

        {mode === 'upload' && (
          <div className="space-y-4">
            <p className="text-xs text-gray-500 dark:text-gray-400">Paste a PEM certificate. Omit the private key to reuse the one generated with your CSR. The private key is stored on the server and never displayed.</p>
            <Field label="Certificate (PEM)"><textarea className={`${inputCls} font-mono text-xs h-32`} value={certPem} onChange={(e) => setCertPem(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" /></Field>
            <Field label="Private key (PEM) — optional if you used Generate CSR"><textarea className={`${inputCls} font-mono text-xs h-24`} value={keyPem} onChange={(e) => setKeyPem(e.target.value)} placeholder="-----BEGIN PRIVATE KEY----- (leave blank to reuse CSR key)" /></Field>
            <Field label="Intermediate chain (PEM) — optional"><textarea className={`${inputCls} font-mono text-xs h-24`} value={chainPem} onChange={(e) => setChainPem(e.target.value)} /></Field>
            <button onClick={doUpload} disabled={busy || !certPem.trim()} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {busy ? 'Uploading…' : 'Upload & Install'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
      {children}
    </div>
  )
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt>
      <dd className={clsx('text-gray-800 dark:text-gray-100 break-all', mono && 'font-mono text-xs')}>{value}</dd>
    </div>
  )
}

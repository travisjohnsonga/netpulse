import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchCACerts, addCACert, deleteCACert, verifyCACert, type CACertificate,
} from '../../api/client'

const EXPIRY_BADGE: Record<string, string> = {
  ok: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  warning: 'bg-orange-50 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  expired: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
  none: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400',
}

function expiryLabel(c: CACertificate): string {
  if (c.expiry_status === 'expired') return 'Expired'
  if (c.days_remaining == null) return 'Unknown'
  if (c.expiry_status === 'warning') return `Expires in ${c.days_remaining}d`
  return `Valid · ${c.days_remaining}d left`
}

export default function TrustedCACerts() {
  const [certs, setCerts] = useState<CACertificate[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [pem, setPem] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setLoading(true)
    fetchCACerts().then(setCerts).catch(() => setError('Failed to load CA certificates.')).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const submit = async () => {
    setBusy(true); setError(null)
    try {
      const updated = await addCACert({ name: name.trim() || undefined, certificate: pem })
      setCerts(updated)
      setAdding(false); setName(''); setPem('')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not add certificate. Expected PEM, DER, or PKCS#7.')
    } finally { setBusy(false) }
  }

  const remove = async (c: CACertificate) => {
    if (!window.confirm(`Remove trusted CA "${c.name}"? Outbound HTTPS will no longer trust it.`)) return
    setBusy(true)
    try { await deleteCACert(c.id); setCerts((p) => p.filter((x) => x.id !== c.id)) }
    finally { setBusy(false) }
  }

  const verify = async (c: CACertificate) => {
    const r = await verifyCACert(c.id)
    window.alert(r.valid ? `Valid — ${r.days_remaining}d remaining.` : `Not valid (${r.expiry_status}).`)
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">
          Trusted CA Certificates
        </h3>
        {!adding && (
          <button onClick={() => setAdding(true)}
            className="px-3 py-1.5 text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white rounded-lg">
            + Add CA certificate
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
        Trust internal/private PKIs and SSL-inspection proxies. Applied to all outbound
        HTTPS (CVE feeds, vendor APIs, git sync) and nginx OCSP stapling.
      </p>

      {error && (
        <div className="mb-3 text-sm bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 rounded-lg px-4 py-2">
          {error}
        </div>
      )}

      {adding && (
        <div className="mb-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
          <input
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Name (optional — defaults to the cert subject)"
            value={name} onChange={(e) => setName(e.target.value)} />
          <textarea
            className="w-full px-3 py-2 text-xs font-mono border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 h-40"
            placeholder="-----BEGIN CERTIFICATE-----&#10;… PEM, base64 DER, or PKCS#7 …&#10;-----END CERTIFICATE-----"
            value={pem} onChange={(e) => setPem(e.target.value)} />
          <div className="flex gap-2">
            <button onClick={submit} disabled={busy || !pem.trim()}
              className="px-3 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {busy ? 'Adding…' : 'Add to trust store'}
            </button>
            <button onClick={() => { setAdding(false); setError(null) }}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700">
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
        {loading ? (
          <div className="px-5 py-6 text-sm text-gray-400">Loading…</div>
        ) : certs.length === 0 ? (
          <div className="px-5 py-6 text-sm text-gray-400 dark:text-gray-500 text-center">
            No custom CA certificates. System root CAs are trusted by default.
          </div>
        ) : certs.map((c) => (
          <div key={c.id} className="flex items-center gap-4 px-5 py-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">{c.name}</span>
                <span className={clsx('text-[10px] px-1.5 py-0.5 rounded', 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300')}>
                  {c.is_root ? 'Root' : c.is_intermediate ? 'Intermediate' : 'Leaf'}
                </span>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{c.subject}</p>
              <p className="text-[11px] text-gray-400 dark:text-gray-500 font-mono truncate">
                SHA-256 {c.fingerprint_sha256.slice(0, 32)}…
              </p>
            </div>
            <span className={clsx('text-xs px-2 py-1 rounded-md shrink-0', EXPIRY_BADGE[c.expiry_status])}>
              {expiryLabel(c)}
            </span>
            <button onClick={() => verify(c)} disabled={busy}
              className="text-xs text-blue-600 dark:text-blue-400 hover:underline shrink-0">Verify</button>
            <button onClick={() => remove(c)} disabled={busy}
              className="text-xs text-red-600 dark:text-red-400 hover:underline shrink-0">Remove</button>
          </div>
        ))}
      </div>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { mfaSetup, mfaConfirm, type MfaSetup, type MfaConfirmResult } from '../api/client'
import { parseApiErrors } from '../api/errors'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const primary =
  'px-4 py-2 text-sm rounded-lg font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 transition-colors'
const ghost =
  'px-4 py-2 text-sm rounded-lg font-medium border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'

const APPS = 'Google Authenticator, Microsoft Authenticator, Authy, 1Password — any TOTP app works.'

/**
 * Enrollment: scan the QR → enter a 6-digit code → confirm → save the one-time
 * recovery codes. Starts setup on mount; the parent decides when to mount it.
 * `enrollmentToken` routes the forced-enrollment path (privileged user, no JWT
 * yet); on confirm the result carries `tokens` for the parent to log in with.
 * The secret and recovery codes are only ever held in component state for
 * display — never persisted or logged.
 */
export default function MfaEnrollmentFlow({
  enrollmentToken,
  onComplete,
  onCancel,
}: {
  enrollmentToken?: string
  onComplete: (result: MfaConfirmResult) => void
  onCancel?: () => void
}) {
  const [setup, setSetup] = useState<MfaSetup | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<MfaConfirmResult | null>(null)
  const [copied, setCopied] = useState<'secret' | 'codes' | null>(null)

  useEffect(() => {
    let active = true
    mfaSetup(enrollmentToken)
      .then((s) => active && setSetup(s))
      .catch((e) => active && setError(parseApiErrors(e, 'Could not start two-factor setup.')))
    return () => { active = false }
  }, [enrollmentToken])

  const confirm = async () => {
    setBusy(true); setError(null)
    try {
      setResult(await mfaConfirm(code.trim(), enrollmentToken))
    } catch (e) {
      setError(parseApiErrors(e, "That code didn't match. Enter the current 6-digit code from your app."))
    } finally { setBusy(false) }
  }

  const copy = async (text: string, which: 'secret' | 'codes') => {
    try { await navigator.clipboard.writeText(text); setCopied(which); setTimeout(() => setCopied(null), 1500) } catch { /* clipboard blocked */ }
  }

  const downloadCodes = (codes: string[]) => {
    const blob = new Blob([`spane two-factor recovery codes\nKeep these somewhere safe. Each works once.\n\n${codes.join('\n')}\n`], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'spane-recovery-codes.txt'; a.click()
    URL.revokeObjectURL(url)
  }

  // ── Step 3: recovery codes (shown once) ──────────────────────────────────
  if (result) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-4 py-3">
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">Save your recovery codes now</p>
          <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
            Shown once. Each code works a single time to sign in if you lose your authenticator. Store them somewhere safe — not in this browser.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 font-mono text-sm">
          {result.recovery_codes.map((c) => (
            <div key={c} className="px-3 py-1.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 text-center tracking-wider">{c}</div>
          ))}
        </div>
        <div className="flex gap-2">
          <button className={ghost} onClick={() => copy(result.recovery_codes.join('\n'), 'codes')}>
            {copied === 'codes' ? 'Copied' : 'Copy codes'}
          </button>
          <button className={ghost} onClick={() => downloadCodes(result.recovery_codes)}>Download</button>
        </div>
        <button className={primary} onClick={() => onComplete(result)}>I&apos;ve saved them — continue</button>
      </div>
    )
  }

  // ── Step 1/2: QR + code entry ────────────────────────────────────────────
  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-3 py-2 text-sm text-red-700 dark:text-red-300">{error}</div>
      )}

      {!setup ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">Preparing setup…</p>
      ) : (
        <>
          <ol className="text-sm text-gray-600 dark:text-gray-400 space-y-1 list-decimal list-inside">
            <li>Open your authenticator app. {APPS}</li>
            <li>Scan this QR code (or enter the key by hand).</li>
            <li>Enter the 6-digit code it shows to finish.</li>
          </ol>

          <div className="flex flex-col sm:flex-row gap-4 sm:items-center">
            <img
              src={setup.qr_code}
              alt="Two-factor setup QR code"
              className="w-40 h-40 rounded-lg bg-white p-2 border border-gray-200 dark:border-gray-700 shrink-0"
            />
            <div className="min-w-0">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Can&apos;t scan? Enter this key:</p>
              <div className="flex items-center gap-2">
                <code className="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 text-xs font-mono break-all text-gray-800 dark:text-gray-200">{setup.secret}</code>
                <button className="text-xs text-blue-600 dark:text-blue-400 hover:underline shrink-0" onClick={() => copy(setup.secret, 'secret')}>
                  {copied === 'secret' ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Verification code</label>
            <input
              className={input}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              onKeyDown={(e) => { if (e.key === 'Enter' && code.length === 6) confirm() }}
            />
          </div>

          <div className="flex gap-2">
            <button className={primary} disabled={busy || code.length !== 6} onClick={confirm}>
              {busy ? 'Verifying…' : 'Verify & enable'}
            </button>
            {onCancel && <button className={ghost} onClick={onCancel} disabled={busy}>Cancel</button>}
          </div>
        </>
      )}
    </div>
  )
}

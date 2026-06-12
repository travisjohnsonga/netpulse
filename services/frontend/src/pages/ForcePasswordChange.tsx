import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { changePassword } from '../api/client'
import { useAuthStore } from '../store/authStore'
import LogoMark from '../components/LogoMark'

/**
 * Full-screen, non-dismissable password change shown when the account is flagged
 * must_change_password (the seeded admin on the fixed default password). There is
 * deliberately no cancel/back — the auth gate in App.tsx keeps the user here
 * until the change succeeds. On success the API returns fresh tokens (with the
 * flag cleared) which we store, dropping the gate.
 */
export default function ForcePasswordChange() {
  const navigate = useNavigate()
  const setTokens = useAuthStore((s) => s.setTokens)
  const username = useAuthStore((s) => s.username)
  const logout = useAuthStore((s) => s.logout)

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const rules = [
    { ok: next.length >= 8, label: 'At least 8 characters' },
    { ok: /[A-Z]/.test(next), label: 'An uppercase letter' },
    { ok: /[0-9]/.test(next), label: 'A number' },
    { ok: next.length > 0 && next !== current, label: 'Different from current password' },
  ]

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (next !== confirm) { setError('New password and confirmation do not match.'); return }
    if (!rules.every((r) => r.ok)) { setError('Password does not meet the requirements below.'); return }
    setLoading(true)
    try {
      const { access, refresh } = await changePassword(current, next)
      if (access && refresh) {
        setTokens(access, refresh)   // fresh tokens clear must_change_password
        navigate('/dashboard', { replace: true })
      } else {
        // No tokens returned (unexpected) — re-auth cleanly.
        logout()
        navigate('/login', { replace: true })
      }
    } catch (err: unknown) {
      const data = (err as { response?: { data?: Record<string, string[] | string> } })?.response?.data
      setError(data ? Object.values(data).flat().join(' ') : 'Failed to update password.')
    } finally {
      setLoading(false)
    }
  }

  const input =
    'w-full px-3 py-2.5 border border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 to-gray-800 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <LogoMark className="w-14 h-14 text-blue-500 rounded-2xl mx-auto mb-4 shadow-lg" />
          <h1 className="text-2xl font-bold text-white">spane</h1>
          <p className="text-gray-400 text-sm mt-1">Set a new password</p>
        </div>

        <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl p-8">
          <div className="mb-5 px-4 py-3 bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-900 rounded-lg text-sm text-amber-800 dark:text-amber-200">
            You must change your password before continuing
            {username ? <> (signed in as <span className="font-medium">{username}</span>)</> : null}.
          </div>

          {error && (
            <div className="mb-4 px-4 py-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Current password</label>
              <input type="password" autoComplete="current-password" value={current}
                onChange={(e) => setCurrent(e.target.value)} className={input} placeholder="spane1!" disabled={loading} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">New password</label>
              <input type="password" autoComplete="new-password" value={next}
                onChange={(e) => setNext(e.target.value)} className={input} placeholder="••••••••" disabled={loading} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Confirm new password</label>
              <input type="password" autoComplete="new-password" value={confirm}
                onChange={(e) => setConfirm(e.target.value)} className={input} placeholder="••••••••" disabled={loading} />
            </div>

            <ul className="text-xs space-y-1 mt-1">
              {rules.map((r) => (
                <li key={r.label} className={r.ok ? 'text-green-600 dark:text-green-400' : 'text-gray-500 dark:text-gray-400'}>
                  {r.ok ? '✓' : '○'} {r.label}
                </li>
              ))}
            </ul>

            <button type="submit" disabled={loading}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg text-sm font-medium transition-colors mt-2 flex items-center justify-center gap-2">
              {loading && <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
              {loading ? 'Updating…' : 'Change password & continue'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

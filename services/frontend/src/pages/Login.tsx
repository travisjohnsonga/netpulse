import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  login, loginMfa, fetchSSOProvidersPublic,
  type SSOProviderPublic, type TokenPair,
} from '../api/client'
import { useAuthStore } from '../store/authStore'
import LogoMark from '../components/LogoMark'
import MfaEnrollmentFlow from '../components/MfaEnrollmentFlow'

/** Brand-ish icon for each SSO provider (lucide-react isn't a dependency). */
function providerIcon(provider: string): ReactNode {
  switch (provider) {
    case 'google-oauth2':
      return <span className="text-base font-bold text-[#4285F4]">G</span>
    case 'azuread-tenant-oauth2':
      return (
        <svg viewBox="0 0 23 23" className="w-4 h-4" aria-hidden>
          <rect width="10" height="10" x="1" y="1" fill="#F25022" />
          <rect width="10" height="10" x="12" y="1" fill="#7FBA00" />
          <rect width="10" height="10" x="1" y="12" fill="#00A4EF" />
          <rect width="10" height="10" x="12" y="12" fill="#FFB900" />
        </svg>
      )
    case 'okta-oauth2':
      return <span className="text-base font-bold text-[#007DC1]">O</span>
    case 'github':
      return (
        <svg viewBox="0 0 16 16" className="w-4 h-4 fill-current" aria-hidden>
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 012-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
        </svg>
      )
    default:
      return <span className="text-base" aria-hidden>🔐</span>
  }
}

const inputCls =
  'w-full px-3 py-2.5 border border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'

/** Shared card chrome for every login step. */
function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 to-gray-800 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <LogoMark className="w-14 h-14 text-blue-500 rounded-2xl mx-auto mb-4 shadow-lg" />
          <h1 className="text-2xl font-bold text-white">spane</h1>
          <p className="text-gray-400 text-sm mt-1">Network Intelligence Platform</p>
        </div>
        <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl p-8">{children}</div>
        <p className="text-center text-xs text-gray-500 mt-6">spane — unified infrastructure visibility</p>
      </div>
    </div>
  )
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 px-4 py-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg text-sm text-red-700 dark:text-red-300">
      {children}
    </div>
  )
}

type Step =
  | { kind: 'login' }
  | { kind: 'mfa'; challengeToken: string }
  | { kind: 'enroll'; enrollmentToken: string }

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const setTokens = useAuthStore((s) => s.setTokens)

  const [step, setStep] = useState<Step>({ kind: 'login' })
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [ssoProviders, setSsoProviders] = useState<SSOProviderPublic[]>([])

  const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? '/dashboard'

  const finishLogin = (t: TokenPair) => {
    setTokens(t.access, t.refresh)
    navigate(t.must_change_password ? '/change-password' : from, { replace: true })
  }

  // The successful SSO token (`/#token=…&refresh=…`) is captured in main.tsx
  // before React renders. Here we only surface the failure case.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('sso_error')) {
      setError('SSO sign-in failed. Try again, or sign in with a username below.')
      window.history.replaceState({}, '', '/login')
    }
  }, [])

  useEffect(() => {
    fetchSSOProvidersPublic().then(setSsoProviders).catch(() => setSsoProviders([]))
  }, [])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username || !password) { setError('Username and password are required.'); return }
    setLoading(true); setError(null)
    try {
      const res = await login(username, password)
      if ('mfa_required' in res) setStep({ kind: 'mfa', challengeToken: res.challenge_token })
      else if ('mfa_enrollment_required' in res) setStep({ kind: 'enroll', enrollmentToken: res.enrollment_token })
      else finishLogin(res)
    } catch {
      setError('Invalid username or password.')
    } finally {
      setLoading(false)
    }
  }

  // ── Second factor ──────────────────────────────────────────────────────────
  if (step.kind === 'mfa') {
    return (
      <Shell>
        <SecondFactor
          challengeToken={step.challengeToken}
          onSuccess={finishLogin}
          onBack={() => { setStep({ kind: 'login' }); setError(null) }}
        />
      </Shell>
    )
  }

  // ── Forced enrollment (privileged local account, no MFA yet) ────────────────
  if (step.kind === 'enroll') {
    return (
      <Shell>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-1">Set up two-factor authentication</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">
          Your account requires two-factor authentication. Set it up now to continue — you won&apos;t be able to sign in until it&apos;s on.
        </p>
        <MfaEnrollmentFlow
          enrollmentToken={step.enrollmentToken}
          onComplete={(r) => { if (r.tokens) finishLogin(r.tokens) }}
        />
      </Shell>
    )
  }

  // ── Username + password ─────────────────────────────────────────────────────
  return (
    <Shell>
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-6">Sign in</h2>
      {error && <Alert>{error}</Alert>}

      {ssoProviders.length > 0 && (
        <div className="mb-6">
          <div className="space-y-3">
            {ssoProviders.map((provider) => (
              <a
                key={provider.id}
                href={provider.login_url}
                className="flex items-center justify-center w-full px-4 py-2.5 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition text-sm font-medium gap-3 text-gray-700 dark:text-gray-200"
              >
                {providerIcon(provider.provider)}
                Sign in with {provider.name}
              </a>
            ))}
          </div>
          <div className="relative my-4">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300 dark:border-gray-600" />
            </div>
            <div className="relative flex justify-center text-xs text-gray-500">
              <span className="px-2 bg-white dark:bg-gray-900">or sign in with username</span>
            </div>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username</label>
          <input type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} className={inputCls} placeholder="admin" disabled={loading} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Password</label>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputCls} placeholder="••••••••" disabled={loading} />
        </div>
        <button type="submit" disabled={loading} className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg text-sm font-medium transition-colors mt-2 flex items-center justify-center gap-2">
          {loading && <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </Shell>
  )
}

function SecondFactor({
  challengeToken, onSuccess, onBack,
}: {
  challengeToken: string
  onSuccess: (t: TokenPair) => void
  onBack: () => void
}) {
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true); setError(null)
    try {
      const tokens = await loginMfa(challengeToken, recovery ? { recovery_code: code.trim() } : { code: code.trim() })
      onSuccess(tokens)
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 429) setError('Too many attempts. Wait a minute, then try again.')
      else if (status === 401) setError('Your sign-in expired. Go back and enter your password again.')
      else setError(recovery ? "That recovery code didn't work." : "That code didn't match. Enter the current 6-digit code from your app.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-1">Two-factor authentication</h2>
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">
        {recovery ? 'Enter one of your recovery codes.' : 'Enter the 6-digit code from your authenticator app.'}
      </p>
      {error && <Alert>{error}</Alert>}
      <form onSubmit={submit} className="space-y-4">
        <input
          autoFocus
          className={inputCls + ' text-center tracking-[0.3em] font-mono'}
          inputMode={recovery ? 'text' : 'numeric'}
          autoComplete="one-time-code"
          maxLength={recovery ? 12 : 6}
          placeholder={recovery ? 'xxxxx-xxxxx' : '123456'}
          value={code}
          onChange={(e) => setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, '').slice(0, 6))}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !code} className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2">
          {loading && <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
          {loading ? 'Verifying…' : 'Verify'}
        </button>
      </form>
      <div className="flex items-center justify-between mt-4 text-xs">
        <button onClick={onBack} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">← Back to sign in</button>
        <button onClick={() => { setRecovery((r) => !r); setCode(''); setError(null) }} className="text-blue-600 dark:text-blue-400 hover:underline">
          {recovery ? 'Use an authenticator code' : 'Use a recovery code'}
        </button>
      </div>
    </>
  )
}
